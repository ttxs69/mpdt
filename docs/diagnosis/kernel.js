/* ============================================================
 * 判定内核 —— 与 Python 侧 mpdt/analysis.py + diagnose.py 保持一致。
 * 任何一侧改判定口径，另一侧必须同步改（见 anti_patterns AP91）。
 * 常量：HIT_REC=20（进推荐池阈值，唯一常量源）
 * ============================================================ */
const HIT_REC = 20;
const MID_LO = 3, MID_HI = 20;
const BLACKOUT_MAX_REC = 3, BLACKOUT_MIN_DAYS = 2;
const SPIKE_FRACTION = 0.25;
const FIN_MIN_ROWS = 10, FIN_MIN_DAYS = 7;


function median(a){ if(!a.length) return 0; const s=[...a].sort((x,y)=>x-y), m=s.length>>1;
  return s.length%2 ? s[m] : (s[m-1]+s[m])/2; }
function pct(a,p){ if(!a.length) return 0; const s=[...a].sort((x,y)=>x-y);
  return s[Math.max(0, Math.round(p*(s.length-1)))]; }

/* ---------- 标题归类：内容家族 ---------- */
function stripTail(t){
  t = t.replace(/[（(]?\s*(?:\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2})\s*[）)]?\s*$/,'')
       .replace(/[（(]?\s*\d+\s*[）)]?\s*$/,'');
  return t.replace(/^[\s　\-—·|]+|[\s　\-—·|]+$/g,'');
}
function titleFamily(title){
  let t = (title||'').trim();
  // 与 Python 侧 analysis.py::title_family **完全一致**：
  // 依次切所有分隔符（不 break），且**不含 '-'**（会切断 'AI-Agent' 这类词）。
  for (const sep of ['·','｜','|','—',':','：']){ if(t.includes(sep)){ t = t.split(sep)[0].trim(); } }
  t = t.split('  ')[0].trim();
  t = stripTail(t);
  return t ? t.slice(0,18) : '(无标题)';
}

/* ---------- 家族分组 + 孤峰判定 ---------- */
function groupRows(rows){
  const fam = new Map();
  for (const r of rows){ const k = titleFamily(r.title);
    if(!fam.has(k)) fam.set(k, []); fam.get(k).push(r); }
  const out = {};
  for (const [name, rs] of fam){
    const recs = rs.map(r=>r.rec||0);
    const hit = rs.filter(r=>(r.rec||0) >= HIT_REC);
    const n = rs.length;
    const spike = hit.length>0 && hit.length <= Math.max(1, n*SPIKE_FRACTION) && Math.max(...recs) >= HIT_REC;
    const spikeRows = spike ? hit.slice().sort((a,b)=>(b.rec||0)-(a.rec||0)) : [];
    out[name] = { n, rec_med: median(recs), rec_max: Math.max(...recs),
      hit_n: hit.length, hit_rate: n? hit.length/n : 0, spike,
      spike_max: spikeRows.length? (spikeRows[0].rec||0) : 0,
      spike_titles: spikeRows.map(r=>r.title), read_med: median(rs.map(r=>r.read||0)) };
  }
  return out;
}
function familyVerdict(rows){
  const fams = groupRows(rows);
  const usable = Object.fromEntries(Object.entries(fams).filter(([k,v])=>v.n>=2));
  const keys = Object.keys(usable);
  if (keys.length < 2) return {verdict:'NONE', families:fams, usable, actions:[], caveats:[]};
  const hiKey = keys.reduce((a,b)=> usable[a].rec_med >= usable[b].rec_med ? a : b);
  const loCands = keys.filter(k=>k!==hiKey);
  if (!loCands.length) return {verdict:'NONE', families:fams, usable, actions:[], caveats:[]};
  const loKey = loCands.reduce((a,b)=> usable[a].rec_med <= usable[b].rec_med ? a : b);
  const hi = usable[hiKey], lo = usable[loKey];
  const ratio = (hi.rec_med+1)/(lo.rec_med+1);
  const baseCav = '家族按标题前缀归类，同名系列会合并；样本 <2 篇的家族不参与';
  if (ratio >= 2.0 && hi.rec_med >= HIT_REC){
    if (lo.spike){
      const titles = lo.spike_titles.map(t=> t.length>40 ? t.slice(0,40)+'…' : t).join('；');
      return {verdict:'FAMILY_SPARK', hi:hiKey, lo:loKey, ratio, families:fams, usable,
        headline:`「${loKey}」不是"完全吃不到推荐"——${lo.n} 篇里有 ${lo.hit_n} 篇吃到（最高 ${lo.spike_max} 推荐阅读），其余为 0。这是**内容事件**的功劳，不是形态的功劳。`,
        actions:[`**别停掉这一形态**——它能爆（最高 ${lo.spike_max} 推荐阅读），只是不常爆。`,
          `把爆过的那 ${lo.hit_n} 篇找出来对比（标题/选题/发布时机），找可复制的变量。${titles?'已知爆款：'+titles:''}`,
          `「${hiKey}」是主力（${hi.n} 篇，推荐中位 ${hi.rec_med}）——排期给它留位置。`,
          `「${loKey}」保持现状但降低频率（${lo.n} 篇里只有 ${lo.hit_n} 篇吃到推荐，中位 ${lo.rec_med}）。`],
        caveats:[baseCav, `「孤峰」判定：该组命中篇数 ≤ 组内篇数/4 且 max ≥ ${HIT_REC}`]};
    }
    return {verdict:'FAMILY_SPLIT', hi:hiKey, lo:loKey, ratio, families:fams, usable,
      headline:`两类内容吃推荐的差距 ${ratio.toFixed(1)} 倍：「${hiKey}」推荐阅读中位 ${hi.rec_med}，「${loKey}」只有 ${lo.rec_med}（${lo.n} 篇全部低于阈值，无孤峰）。`,
      actions:[`把「${hiKey}」当主力（${hi.n} 篇，推荐阅读中位 ${hi.rec_med}）——下周的排期给它留位置。`,
        `「${loKey}」先停发或大改（${lo.n} 篇，推荐阅读中位 ${lo.rec_med}，0 篇吃到推荐）。`,
        `别在这两类之间平均用力——这 ${ratio.toFixed(1)} 倍差距是分发侧的偏好，不是内容质量的连续刻度。`],
      caveats:[baseCav,'只有「组内无孤峰」才判停发；有孤峰走「家族有孤峰」']};
  }
  return {verdict:'NONE', families:fams, usable, actions:[],
    headline:'家族之间没有 2 倍以上的推荐差异，暂不判优劣。', caveats:[baseCav]};
}

/* ---------- 断供检测 ---------- */
function detectBlackout(rows){
  const byDay = new Map();
  for (const r of rows){ if(!byDay.has(r.date)) byDay.set(r.date,[]); byDay.get(r.date).push(r.rec||0); }
  const days = [...byDay.keys()].sort();
  const blackDays = days.filter(d=> byDay.get(d).length>=2 && Math.max(...byDay.get(d)) < BLACKOUT_MAX_REC);
  const windows = []; let cur = [];
  for (const d of days){
    if (blackDays.includes(d)) cur.push(d);
    else { if (cur.length >= BLACKOUT_MIN_DAYS) windows.push({from:cur[0],to:cur[cur.length-1],days:cur.length}); cur=[]; }
  }
  if (cur.length >= BLACKOUT_MIN_DAYS) windows.push({from:cur[0],to:cur[cur.length-1],days:cur.length});
  const inWin = new Set();
  for (const w of windows) for (const d of blackDays) if (d>=w.from && d<=w.to) inWin.add(d);
  return {windows, black_days:[...inWin].sort(),
    clean_rows: rows.filter(r=>!inWin.has(r.date)), blackout_rows: rows.filter(r=>inWin.has(r.date))};
}

/* ---------- 主判定 ---------- */
function analyse(rows){
  const n = rows.length;
  const days = new Set(rows.map(r=>r.date)).size;
  const reads = rows.map(r=>r.read||0), recs = rows.map(r=>r.rec||0), msgs = rows.map(r=>r.msg||0);
  const fins = rows.filter(r=>typeof r.fin === 'number' && !isNaN(r.fin)).map(r=>r.fin);
  const hitN = recs.filter(v=>v>=HIT_REC).length;
  const midN = recs.filter(v=>v>=MID_LO && v<HIT_REC).length;
  const totRead = reads.reduce((a,b)=>a+b,0), totRec = recs.reduce((a,b)=>a+b,0), totMsg = msgs.reduce((a,b)=>a+b,0);
  const bo = detectBlackout(rows);
  const clean = bo.clean_rows;
  const cRecs = clean.map(r=>r.rec||0);
  const cHit = cRecs.filter(v=>v>=HIT_REC).length;
  const fam = familyVerdict(rows);
  const groups = groupByHit(rows);
  return { n, days, read_med: median(reads), read_p90: pct(reads,0.9),
    rec_share: totRead? totRec/totRead : 0, msg_share: totRead? totMsg/totRead : 0,
    hit_n: hitN, hit_rate: n? hitN/n : 0, mid_n: midN, mid: n? midN/n : 0,
    fin_med: fins.length? median(fins) : null, fin_n: fins.length,
    blackout: {windows: bo.windows, n_black: bo.blackout_rows.length, n_clean: clean.length},
    hit_rate_clean: clean.length? cHit/clean.length : (n? hitN/n:0), hit_n_clean: cHit,
    groups, family: fam };
}
function groupByHit(rows){
  const hit = rows.filter(r=>(r.rec||0)>=HIT_REC), miss = rows.filter(r=>(r.rec||0)<HIT_REC);
  const stat = rs => ({ n: rs.length, read_med: median(rs.map(r=>r.read||0)),
    rec_med: median(rs.map(r=>r.rec||0)), msg_med: median(rs.map(r=>r.msg||0)),
    fin_med: (()=>{ const f=rs.filter(r=>typeof r.fin==='number').map(r=>r.fin); return f.length?median(f):null; })() });
  return { hit: stat(hit), miss: stat(miss) };
}
function branch(m){
  const hr = m.hit_rate_clean, mid = m.mid;
  if (m.rec_share < 0.20) return {code:'STOCK_ONLY', label:'消息为主型'};
  if (mid > 0.30 && hr < 0.40) return {code:'MID_BAND', label:'中间带型'};
  if (hr < 0.40) return {code:'LOTTERY_STRUCTURE', label:'抽签结构型'};
  return {code:'MIXED', label:'混合型'};
}
function confidenceNote(days, n){
  if (days>=21 && n>=40) return `（窗口 ${days} 天 / ${n} 篇：结构结论可信，可用于季度排期。）`;
  if (days>=14 && n>=20) return `（窗口 ${days} 天 / ${n} 篇：结构结论可信，可用于月度排期。）`;
  return `（窗口仅 ${days} 天 / ${n} 篇：以下结论**只用于本周排期**，别当成长期结构。建议再导出 1 个月的数据复跑一次确认。）`;
}

/* ============================================================
 * 解析：CSV + XLSX（零依赖，用浏览器 DecompressionStream 解 zip）
 * ============================================================ */
function parseCSV(text){
  const rows=[]; let row=[], cell='', q=false;
  for (let i=0;i<text.length;i++){
    const c=text[i];
    if (q){
      if (c==='"'){ if(text[i+1]==='"'){ cell+='"'; i++; } else q=false; }
      else cell+=c;
    } else {
      if (c==='"') q=true;
      else if (c===','){ row.push(cell); cell=''; }
      else if (c==='\n'){ row.push(cell); rows.push(row); row=[]; cell=''; }
      else if (c==='\r'){}
      else cell+=c;
    }
  }
  if (cell.length||row.length){ row.push(cell); rows.push(row); }
  return rows;
}
async function unzip(buf){
  const dv = new DataView(buf), u8 = new Uint8Array(buf);
  // 找 End of Central Directory
  let eocd=-1;
  for (let i=u8.length-22; i>=Math.max(0,u8.length-66000); i--){
    if (dv.getUint32(i,true)===0x06054b50){ eocd=i; break; }
  }
  if (eocd<0) throw new Error('不是有效的 xlsx（找不到 zip 结尾）');
  const count = dv.getUint16(eocd+10,true);
  let off = dv.getUint32(eocd+16,true);
  const files = {};
  for (let i=0;i<count;i++){
    if (dv.getUint32(off,true)!==0x02014b50) break;
    const method = dv.getUint16(off+10,true);
    const compSize = dv.getUint32(off+20,true);
    const nameLen = dv.getUint16(off+28,true);
    const extraLen = dv.getUint16(off+30,true);
    const cmtLen = dv.getUint16(off+32,true);
    const localOff = dv.getUint32(off+42,true);
    const name = new TextDecoder().decode(u8.subarray(off+46, off+46+nameLen));
    // 本地头
    const lNameLen = dv.getUint16(localOff+26,true);
    const lExtraLen = dv.getUint16(localOff+28,true);
    const dataStart = localOff + 30 + lNameLen + lExtraLen;
    const raw = u8.subarray(dataStart, dataStart+compSize);
    files[name] = {method, raw};
    off += 46 + nameLen + extraLen + cmtLen;
  }
  const out = {};
  for (const [name, f] of Object.entries(files)){
    if (f.method === 0) out[name] = f.raw;
    else if (f.method === 8){
      const ds = new DecompressionStream('deflate-raw');
      const stream = new Blob([f.raw]).stream().pipeThrough(ds);
      out[name] = new Uint8Array(await new Response(stream).arrayBuffer());
    }
  }
  return out;
}
function xmlDecode(s){
  // 防御：自闭合单元格（如 <c r="A1"/>）的 body 是 undefined
  return String(s ?? '').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"')
          .replace(/&apos;/g,"'").replace(/&amp;/g,'&');
}
function parseXMLText(xml, tag){
  // **不要用 match + /g**：`String.match` 传全局正则时返回**全部匹配**，
  // `m[1]` 会是 undefined（只有一个匹配时）⇒ 所有 <v> 数值单元格读成空
  // ⇒ 真实 Excel 导出的数字列（阅读人数/推荐）全部丢失。
  // 必须用 exec + 非全局正则。
  const re = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`);
  const m = re.exec(xml || ''); return m ? m[1] : '';
}
async function readXlsx(buf){
  const files = await unzip(buf);
  const dec = new TextDecoder();
  // sharedStrings
  let shared = [];
  if (files['xl/sharedStrings.xml']){
    const sxml = dec.decode(files['xl/sharedStrings.xml']);
    const siRe = /<si>([\s\S]*?)<\/si>/g; let m;
    while ((m = siRe.exec(sxml))){
      const tRe = /<t[^>]*>([\s\S]*?)<\/t>/g; let t, s='';
      while ((t = tRe.exec(m[1]))) s += t[1];
      shared.push(xmlDecode(s));
    }
  }
  // sheet1
  let sheetName = Object.keys(files).find(k=>/^xl\/worksheets\/sheet1\.xml$/.test(k))
              || Object.keys(files).find(k=>/^xl\/worksheets\/.*\.xml$/.test(k));
  if (!sheetName) throw new Error('xlsx 里没找到工作表');
  const xml = dec.decode(files[sheetName]);
  const rows = [];
  const rowRe = /<row[^>]*>([\s\S]*?)<\/row>/g; let rm;
  while ((rm = rowRe.exec(xml))){
    const cells = []; const cRe = /<c([^>]*)(?:\/>|>([\s\S]*?)<\/c>)/g; let cm;
    while ((cm = cRe.exec(rm[1]))){
      const attrs = cm[1]||'', body = cm[2]||'';
      const ref = (attrs.match(/r="([A-Z]+)\d+"/)||[])[1] || '';
      let col = 0;
      for (const ch of ref){ col = col*26 + (ch.charCodeAt(0)-64); }
      col = Math.max(0, col-1);
      const t = (attrs.match(/t="([^"]+)"/)||[])[1];
      let val = '';
      if (t === 'inlineStr'){ const tRe=/<t[^>]*>([\s\S]*?)<\/t>/g; let x; while((x=tRe.exec(body))) val += x[1]; }
      else if (t === 's'){ const v = parseXMLText(body,'v'); val = shared[parseInt(v)||0] ?? ''; }
      else { const v = parseXMLText(body,'v'); val = v; }
      cells[col] = xmlDecode(val);
    }
    rows.push(cells);
  }
  return rows;
}

/* ---------- 表头识别 + 列映射（与 Python 侧一致） ---------- */
const COLS = {
  date:  ['日期','发布时间','发表时间','发表日','推送时间'],
  title: ['标题','内容标题','文章标题','题目'],
  read:  ['阅读人数','阅读量','阅读次数','总阅读','送达人数'],
  rec:   ['推荐','推荐阅读','推荐来源'],
  msg:   ['公众号消息','消息','会话来源'],
  fin:   ['完读率','阅读完成率','完成率'],
};
const BAD = ['推荐位','推荐语','推荐类型','推荐设置'];
// 与 Python 侧 xlsx_to_input.py::_clean 保持一致：
// **只 strip，不删内部空格**（早期版本删空格导致标题变成「rea登顶GHTrending」，
// 与 Python 侧家族名不一致 ⇒ 同一号两条路径结论不同 = AP91 分叉）。
// 全角空格 → 半角（Python 侧同样处理）。
function clean(c){ return String(c??'').replace(/\u3000/g,' ').trim(); }
function findHeader(rows){
  for (let i=0;i<Math.min(10,rows.length);i++){
    const cs = rows[i].map(clean);
    if (cs.some(c=>c.includes('日期')||c.includes('时间')) &&
        cs.some(c=>c.includes('阅读')||c.includes('标题'))) return i;
  }
  return null;
}
function mapColumns(header){
  const map = {};
  for (const [k, aliases] of Object.entries(COLS)){
    for (let i=0;i<header.length;i++){
      const c = clean(header[i]); if (!c) continue;
      if (BAD.some(b=>c.includes(b))) continue;
      if (aliases.some(a=>c===a || c.includes(a))){ if(!(k in map)) map[k]=i; break; }
    }
  }
  return map;
}
function parseNumber(s){
  if (s===null||s===undefined) return null;
  let t = String(s).replace(/[,\s，]/g,'').replace(/%$/,'');
  if (t===''||t==='-'||t==='—') return null;
  const v = parseFloat(t);
  return isNaN(v) ? null : v;
}
function convert(rows){
  const hi = findHeader(rows);
  if (hi===null) throw new Error('前 10 行里找不到表头（需要含「日期」和「标题/阅读」的列）。是不是导错了文件？');
  const header = rows[hi]||[];
  const map = mapColumns(header);
  const missing = ['date','read','rec','msg'].filter(k=>!(k in map));
  if (missing.length) throw new Error(
    `缺关键列 ${missing.join('、')}。\n识别到的表头：${header.map(clean).filter(Boolean).slice(0,12).join(' | ')}\n` +
    `需要：日期 / 标题 / 阅读人数 / 推荐 / 公众号消息。\n` +
    `注意：只导「已通知内容」列表往往没有分渠道数据，要导「已发表内容」的明细表。`);
  const arts=[], skipped=[];
  for (let r=hi+1;r<rows.length;r++){
    const row = rows[r]||[];
    if (!row.some(c=>clean(c))) continue;
    const g = k => (map[k]!==undefined && map[k]<row.length) ? clean(row[map[k]]) : '';
    const title = g('title') || '(无标题)';
    const read = parseNumber(g('read'));
    const rec = parseNumber(g('rec')) || 0;
    const msg = parseNumber(g('msg')) || 0;
    if (read===null || read<=0){ skipped.push({title:title.slice(0,40), reason:'阅读人数缺失或 ≤0'}); continue; }
    if (rec+msg > read+1e-9){
      skipped.push({title:title.slice(0,40),
        reason:`推荐+消息(${Math.round(rec+msg)}) > 总阅读(${Math.round(read)})：这一行的渠道列口径与总读不一致`});
      continue;
    }
    const a = {date:g('date'), title, read, rec, msg};
    const fin = parseNumber(g('fin'));
    if (fin!==null) a.fin = fin>1 ? fin/100 : fin;
    arts.push(a);
  }
  return {arts, skipped, mapping: Object.fromEntries(Object.entries(map).map(([k,v])=>[k, clean(header[v])]))};
}
/* 导出：浏览器挂 window，node 用 module.exports */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { HIT_REC, MID_LO, MID_HI, BLACKOUT_MAX_REC, BLACKOUT_MIN_DAYS,
    SPIKE_FRACTION, median, pct, stripTail, titleFamily, groupRows, familyVerdict,
    detectBlackout, analyse, groupByHit, branch, confidenceNote,
    parseCSV, unzip, xmlDecode, parseXMLText, readXlsx, clean, findHeader,
    mapColumns, parseNumber, convert };
}
