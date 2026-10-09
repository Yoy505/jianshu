// 展示格式化工具：价格（分转元）、日期友好化、手机号校验

function pad(n) {
  return n < 10 ? '0' + n : '' + n;
}

// 分 → 元展示字符串：3900→"39"，3950→"39.5"，26800→"268"，999→"9.99"
function price(cents) {
  if (cents === null || cents === undefined || cents === '') return '--';
  const n = Number(cents);
  if (isNaN(n)) return '--';
  let s = String(Math.round((n / 100) * 1000) / 1000);
  if (s.indexOf('.') > -1) {
    s = s.replace(/0+$/, '').replace(/\.$/, '');
  }
  return s;
}

function parseISO(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? null : d;
}

// "14:05"
function timeOf(iso) {
  const d = parseISO(iso);
  return d ? pad(d.getHours()) + ':' + pad(d.getMinutes()) : '';
}

// "2026-09-21 14:05"
function full(iso) {
  const d = parseISO(iso);
  if (!d) return '';
  return (
    d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + timeOf(iso)
  );
}

// 友好时间：刚刚 / n分钟前 / 今天时刻 / 昨天 / M月D日 / YYYY-MM-DD
function friendly(iso) {
  const d = parseISO(iso);
  if (!d) return '';
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  if (diff >= 0 && diff < 60 * 1000) return '刚刚';
  if (diff >= 0 && diff < 60 * 60 * 1000) return Math.floor(diff / 60000) + '分钟前';
  if (d.toDateString() === now.toDateString()) return timeOf(iso);
  const yest = new Date(now.getTime() - 24 * 60 * 60 * 1000);
  if (d.toDateString() === yest.toDateString()) return '昨天';
  if (d.getFullYear() === now.getFullYear()) {
    return d.getMonth() + 1 + '月' + d.getDate() + '日';
  }
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
}

// "2026-09-24" → "09-24"
function mdOf(dateStr) {
  const p = String(dateStr || '').split('-');
  return p.length >= 3 ? p[1] + '-' + p[2] : String(dateStr);
}

// "2026-09-24" → "周四"
function weekOf(dateStr) {
  const p = String(dateStr || '').split('-');
  if (p.length < 3) return '';
  const d = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
  return '周' + '日一二三四五六'.charAt(d.getDay());
}

function phoneValid(p) {
  return /^1[3-9]\d{9}$/.test(String(p || ''));
}

function thousands(n) {
  return String(n == null ? 0 : n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

// 评分统一一位小数
function rating(n) {
  const v = Number(n);
  return isNaN(v) ? '--' : v.toFixed(1);
}

module.exports = {
  pad,
  price,
  timeOf,
  full,
  friendly,
  mdOf,
  weekOf,
  phoneValid,
  thousands,
  rating
};
