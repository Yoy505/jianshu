"""本地开发演示用的极简 Redis 替身（无 Docker/Redis 环境时使用）。

实现 RESP2/RESP3 协议中本项目实际用到的最小命令集：
HELLO/PING/CLIENT/SELECT/GET/SET(EX,NX)/SETEX/DEL/INCR/EXPIRE/TTL/EXISTS/KEYS/
LPUSH/LLEN/INFO/DBSIZE/FLUSHDB/FLUSHALL/COMMAND/QUIT
（足够 FastAPI 短信限流、热榜缓存与 Celery 投递使用；不支持 pubsub、事务等）

用法：python dev_fake_redis.py [port]   # 默认 6379
注意：仅限本地开发联调，请勿用于生产。

额外便利：写入 sms:{phone}:code 时会把验证码打印到控制台，方便演示登录流程。
"""

import asyncio
import fnmatch
import sys
import time
from dataclasses import dataclass, field

LOG_CODES_TO_CONSOLE = True

# NIL 哨兵：按连接协议编码为 RESP2 的 $-1 或 RESP3 的 _
NIL = object()


@dataclass
class Entry:
    value: bytes | list
    expires_at: float | None = None  # monotonic 时间戳；None = 永不过期


@dataclass
class Store:
    data: dict[str, Entry] = field(default_factory=dict)


STORE = Store()


# ───────── RESP 编码 ─────────
def simple(s: str) -> bytes:
    return f"+{s}\r\n".encode()


def error(s: str) -> bytes:
    return f"-{s}\r\n".encode()


def integer(n: int) -> bytes:
    return f":{n}\r\n".encode()


def bulk(data: bytes | str | None) -> bytes:
    if data is None:
        return b"$-1\r\n"
    if isinstance(data, str):
        data = data.encode()
    return b"$%d\r\n%s\r\n" % (len(data), data)


def array(items: list) -> bytes:
    out = f"*{len(items)}\r\n".encode()
    for item in items:
        out += bulk(item)
    return out


def ttl_of(key: str) -> int:
    entry = STORE.data.get(key)
    if entry is None:
        return -2
    if entry.expires_at is None:
        return -1
    return max(0, int(entry.expires_at - time.monotonic()))


def _log_sms_code(key: str, value: bytes) -> None:
    if LOG_CODES_TO_CONSOLE and key.startswith("sms:") and key.endswith(":code"):
        phone = key.split(":")[1]
        print(f"[fake-redis] 验证码 {phone} -> {value.decode().split('#')[0]}", flush=True)


def handle_command(parts: list[bytes], proto3: bool) -> bytes | object:
    """返回 bytes 应答；NIL 哨兵由连接层按协议编码。"""
    cmd = parts[0].decode().upper()
    args = parts[1:]

    if cmd == "HELLO":
        # redis-py ≥6 默认 RESP3；kombu(Celery) 走同步 redis-py 亦为 RESP3。
        # 真 Redis 行为：HELLO 应答为 map（RESP3 用 %N，RESP2 用扁平数组 *N），
        # 其中 proto/id 字段为整数类型（redis-py 会校验 proto 必须等于请求值）。
        ver = int(args[0]) if args and args[0].isdigit() else (3 if proto3 else 2)
        fields = [
            (b"server", b"redis", False),
            (b"version", b"7.0.0-fake", False),
            (b"proto", ver, True),
            (b"id", 1, True),
            (b"mode", b"standalone", False),
            (b"role", b"master", False),
            (b"modules", [], "array"),
        ]
        out = b"%%%d\r\n" % len(fields) if ver >= 3 else b"*%d\r\n" % (len(fields) * 2)
        for key, val, kind in fields:
            if ver >= 3:
                out += bulk(key)
            if kind == "array":
                out += array(val)
            elif kind is True:
                out += integer(val)
            else:
                out += bulk(val)
        return out
    if cmd in ("QUIT",):
        return simple("OK")
    if cmd == "PING":
        return simple("PONG")
    if cmd == "CLIENT":  # SETINFO 等：一律应答 OK（客户端可容忍）
        return simple("OK")
    if cmd == "COMMAND":
        return array([])
    if cmd == "SELECT":
        return simple("OK")
    if cmd == "ECHO":
        return bulk(args[0])

    if cmd == "GET":
        key = args[0].decode()
        if STORE.data.get(key) is None or _expired(key):
            return NIL
        entry = STORE.data[key]
        if isinstance(entry.value, list):
            return NIL
        return bulk(entry.value)
    if cmd == "SET":
        key = args[0].decode()
        value, i = args[1], 2
        ex: int | None = None
        nx = False
        while i < len(args):
            opt = args[i].decode().upper()
            if opt == "EX":
                ex = int(args[i + 1]); i += 2
            elif opt == "PX":
                ex = max(1, int(args[i + 1]) // 1000); i += 2
            elif opt == "NX":
                nx = True; i += 1
            elif opt in ("XX", "KEEPTTL", "GET"):
                i += 1  # 演示场景用不到，忽略
            else:
                return error("ERR syntax error")
        if nx and key in STORE.data and not _expired(key):
            return NIL
        expires = time.monotonic() + ex if ex else None
        STORE.data[key] = Entry(value, expires)
        _log_sms_code(key, value)
        return simple("OK")
    if cmd == "SETEX":
        key = args[0].decode()
        STORE.data[key] = Entry(args[2], time.monotonic() + int(args[1]))
        return simple("OK")
    if cmd in ("DEL", "UNLINK"):
        removed = 0
        for raw in args:
            key = raw.decode()
            if key in STORE.data:
                del STORE.data[key]; removed += 1
        return integer(removed)
    if cmd == "EXISTS":
        n = 0
        for raw in args:
            key = raw.decode()
            if key in STORE.data and not _expired(key):
                n += 1
        return integer(n)
    if cmd in ("INCR", "DECR", "INCRBY", "DECRBY"):
        # redis-py 的 incr()/decr() 实际发送 INCRBY/DECRBY
        key = args[0].decode()
        amount = 1
        if cmd in ("INCRBY", "DECRBY"):
            amount = int(args[1])
        if cmd in ("DECR", "DECRBY"):
            amount = -amount
        entry = STORE.data.get(key) if not _expired(key) else None
        cur = 0 if entry is None or isinstance(entry.value, list) else int(entry.value)
        nxt = cur + amount
        STORE.data[key] = Entry(str(nxt).encode(), entry.expires_at if entry else None)
        return integer(nxt)
    if cmd == "EXPIRE":
        key = args[0].decode()
        entry = STORE.data.get(key) if not _expired(key) else None
        if entry is None:
            return integer(0)
        entry.expires_at = time.monotonic() + int(args[1])
        return integer(1)
    if cmd == "TTL":
        return integer(ttl_of(args[0].decode()))
    if cmd == "KEYS":
        pattern = args[0].decode()
        return array([k for k in STORE.data if not _expired(k) and fnmatch.fnmatch(k, pattern)])
    if cmd == "LPUSH":
        key = args[0].decode()
        entry = STORE.data.get(key) if not _expired(key) else None
        lst = entry.value if entry and isinstance(entry.value, list) else []
        lst = list(args[1:]) + lst
        STORE.data[key] = Entry(lst)
        return integer(len(lst))
    if cmd == "LLEN":
        key = args[0].decode()
        entry = STORE.data.get(key) if not _expired(key) else None
        if entry is None or not isinstance(entry.value, list):
            return integer(0)
        return integer(len(entry.value))
    if cmd == "PUBLISH":  # 无订阅者，返回接收数 0
        return integer(0)
    if cmd in ("SADD", "SREM", "SMEMBERS", "SISMEMBER"):
        key = args[0].decode()
        entry = STORE.data.get(key) if not _expired(key) else None
        members = entry.value if entry and isinstance(entry.value, list) else []
        if cmd == "SADD":
            added = 0
            for raw in args[1:]:
                if raw not in members:
                    members.append(raw); added += 1
            STORE.data[key] = Entry(members)
            return integer(added)
        if cmd == "SREM":
            removed = 0
            for raw in args[1:]:
                if raw in members:
                    members.remove(raw); removed += 1
            STORE.data[key] = Entry(members)
            return integer(removed)
        if cmd == "SMEMBERS":
            return array(members)
        return integer(1 if args[1] in members else 0)
    if cmd in ("LPOP", "RPOP"):
        key = args[0].decode()
        entry = STORE.data.get(key) if not _expired(key) else None
        if entry is None or not isinstance(entry.value, list) or not entry.value:
            return NIL
        value = entry.value.pop(0) if cmd == "LPOP" else entry.value.pop()
        STORE.data[key] = entry
        return bulk(value)
    if cmd == "DBSIZE":
        return integer(len(STORE.data))
    if cmd in ("FLUSHDB", "FLUSHALL"):
        STORE.data.clear()
        return simple("OK")
    if cmd == "INFO":
        return bulk("# Server\r\nredis_version:7.0.0-fake\r\n")
    return error(f"ERR unknown command '{cmd}'")


def _expired(key: str) -> bool:
    entry = STORE.data.get(key)
    if entry is None:
        return False
    if entry.expires_at is not None and entry.expires_at <= time.monotonic():
        del STORE.data[key]
        return True
    return False


def encode_reply(reply, proto3: bool) -> bytes:
    if reply is NIL:
        return b"_\r\n" if proto3 else b"$-1\r\n"
    return reply


async def handle_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    proto3 = False
    txn: list[bytes] | None = None  # MULTI 开启后的命令缓冲（kombu 投递用 pipeline）
    while True:
        line = await reader.readline()
        if not line:
            break
        line = line.strip()
        if not line.startswith(b"*"):
            writer.write(b"-ERR protocol error\r\n")
            break
        n = int(line[1:])
        parts = []
        for _ in range(n):
            header = (await reader.readline()).strip()
            length = int(header[1:])
            payload = (await reader.readexactly(length + 2))[:-2]
            parts.append(payload)
        try:
            head = parts[0].decode().upper()
            if head == "HELLO":
                ver = int(parts[1]) if len(parts) > 1 and parts[1].decode().isdigit() else 2
                proto3 = ver >= 3
            if head == "MULTI":
                txn = []
                reply = simple("OK")
            elif txn is not None and head not in ("EXEC", "DISCARD"):
                txn.append(parts)
                reply = simple("QUEUED")
            elif head == "EXEC":
                if txn is None:
                    reply = error("ERR EXEC without MULTI")
                else:
                    results = [encode_reply(handle_command(p, proto3), proto3) for p in txn]
                    txn = None
                    reply = b"*%d\r\n" % len(results) + b"".join(results)
            elif head == "DISCARD":
                txn = None
                reply = simple("OK")
            else:
                reply = encode_reply(handle_command(parts, proto3), proto3)
            writer.write(reply)
            await writer.drain()
        except (ValueError, IndexError):
            writer.write(b"-ERR bad arguments\r\n")
            await writer.drain()


async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 6379
    server = await asyncio.start_server(handle_conn, "127.0.0.1", port)
    print(f"[fake-redis] listening on 127.0.0.1:{port}（本地演示用，命令集最小化）", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
