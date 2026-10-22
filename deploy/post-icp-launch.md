# 杏临汇备案完成后的上线执行单

更新：2026-10-15。用户提供的备案截图显示网站备案号、域名与实例 IP；本文只记录公开上线所需信息，不保存截图中的个人证件、电话或邮箱。本文件不代表网站已上线。

## 当前检查结果

| 项目 | 结果 |
|---|---|
| 规划域名 | `mp.singlin.cn`（API/WS）、`essay.singlin.cn`（征集网页）、`admin.singlin.cn`（后台） |
| 备案记录 | 网站名称“健树征文服务平台”；网站备案号 `津ICP备2026014961号-1`；域名 `singlin.cn` |
| DNS | 阿里云控制台中 `@`、`mp`、`essay`、`admin` 四条 A 记录均指向同一 ECS 实例 IP（本地记录，公开版脱敏），状态为启用；主域已由权威 DNS 返回该 IP。解析完成不代表网站服务已上线。 |
| 服务器端口 | `<服务器IP>:22` 可连接；80、443 拒绝连接；尚不能通过域名访问业务 |
| 本地代码 | H5、后台生产构建通过；后端 health、生产安全、职业健康投稿相关测试 33 项通过 |
| ICP 展示 | H5 和后台已内置截图中的网站备案号，并链接工信部备案查询；必要时可用构建变量 `VITE_ICP_NUMBER` 覆盖 |
| 投稿开关 | 保持 `OCCUPATIONAL_SUBMISSIONS_ENABLED=false`，待真实线上业务验收后再决定开放 |

## 需要核对的资料

1. ECS 的 SSH 用户、已配置的密钥/别名及服务器当前 Nginx/宝塔安装情况；不要发送密码或私钥内容。
2. 一个用于证书续期提醒的管理员邮箱；主域 `singlin.cn` 是否需要跳转到征集网页。主域 A 记录已添加；若要对外提供 HTTPS 页面或跳转，还须在证书与 Nginx 配置中包含主域。
3. 活动主办方确认的正式名称、投稿起止时间、隐私告知与作品公开展示授权文案。
4. 备案网站名称是“健树征文服务平台”，当前公开页面品牌为“杏临汇”；发布前由备案负责人核对网站名称与实际内容的一致性。页面页脚已加入备案网站名称。如备案登记项目需调整，走变更备案流程。

## 服务器操作顺序

以下操作在确认服务器身份和取得授权登录后执行；每一步验证通过后再进入下一步。

1. 核对安全组与系统防火墙，放行公网 80/443，API 8000、PostgreSQL 5432、Redis 6379 仅供本机或内网访问。复核主域及三个子域的 A 记录指向同一台 ECS。
2. 将 `nginx-singlin-bootstrap.conf` 安装到 Nginx `http {}` 中的 include 目录（如 `/etc/nginx/conf.d/singlin.conf`），创建 `/var/www/letsencrypt`，执行 `nginx -t` 并重载。确保 80 端口 ACME 路径可从公网访问。
3. 使用服务器已安装的 Certbot，以 webroot `/var/www/letsencrypt` 签发包含 `mp.singlin.cn`、`essay.singlin.cn`、`admin.singlin.cn` 的证书，指定证书名称 `singlin.cn`；使用实际管理员邮箱。确认定时续期和一次 `certbot renew --dry-run`。
4. 将 bootstrap 文件替换为 `nginx-singlin-production.conf`。该文件须作为整体放入 `http {}` include 目录，不要与旧模板同时启用。用 `nginx -t` 校验后重载；验证 HTTP 到 HTTPS 跳转、证书覆盖三个域名和 `/health`。
5. 部署后端：建立生产数据库与 Redis、私有投稿目录及备份目录；配置 `APP_ENV=prod`、强随机 `JWT_SECRET`、真实微信凭据、三个域名所需 CORS、私有目录路径和 `OCCUPATIONAL_SUBMISSIONS_ENABLED=false`。执行迁移、启动单进程 API 与所需后台任务；严禁运行演示种子。校验 API 只监听 `127.0.0.1:8000`。
6. 构建 H5、后台并将各自 `dist/` 发布至 Nginx 指定目录。核对网页页脚的真实备案号和链接；备案信息发生变更时再使用 `VITE_ICP_NUMBER` 覆盖并重新构建。
7. HTTPS 全通后再将小程序请求地址切换到 `https://mp.singlin.cn/api/v1`，在微信后台配置 request 与 socket 域名，并在正式构建中启用域名校验。真机验收登录、消息连接和各项实际开放功能。
8. 完成线上投稿与后台权限、私有附件下载、文件校验、回执查询、日志、备份恢复和告警验收。只有主办方确认活动规则与隐私文本，并完成真实投稿闭环后，才打开投稿开关。
9. 网站开通后按主管部门要求办理公安联网备案；取得公安备案编号后，再将其标注和查询链接加入网站底部。

## 发布后验收命令

```bash
dig +short A singlin.cn
dig +short A mp.singlin.cn
dig +short A essay.singlin.cn
dig +short A admin.singlin.cn
curl -fsSI https://mp.singlin.cn/health
curl -fsSI https://essay.singlin.cn/
curl -fsSI https://admin.singlin.cn/
curl -fsS https://essay.singlin.cn/api/v1/h5/occupational-submissions/meta
curl -sI https://mp.singlin.cn/uploads/exports/ | head -1
```

发布前 `meta.enabled` 应为 `false`，旧公开导出路径应返回 404。`/health` 为服务存活检查，不替代数据库、Redis、文件处理及真实投稿闭环验收。

## 参考

- [阿里云：ICP备案完成后添加备案号](https://help.aliyun.com/zh/icp-filing/basic-icp-service/the-icp-record-post-processing-1)
- [阿里云：网站开通后的公安联网备案](https://help.aliyun.com/zh/icp-filing/basic-icp-service/the-public-security-network-for-the-record-information-fill-in-the-guide)
- [阿里云：A 记录与验证](https://help.aliyun.com/zh/dns/beginner-s-guide)
- [阿里云：备案网站名称与首页展示](https://help.aliyun.com/zh/icp-filing/basic-icp-service/support/fill-in-the-information-and-website-faq)
