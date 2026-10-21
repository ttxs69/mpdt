"use strict";
/* DOM 辅助（原先在 kernel.js 里，拆分时漏带 ⇒ $ undefined） */
const $ = s => document.querySelector(s);
const el = (t, c, txt) => { const e = document.createElement(t); if (c) e.className = c;
                             if (txt !== undefined) e.textContent = txt; return e; };

/* ============================================================
 * 渲染
 * ============================================================ */
function render(m, skipped, mapping){
  const out = $('#out'); out.classList.remove('hide');
  const br = branch(m);
  const box = el('div');
  box.appendChild(el('h2', null, '结论'));
  const head = el('div','card');
  head.innerHTML = `<div class="big">${br.label} <span class="tag t-acc">${br.code}</span></div>
    <div class="note" style="margin-top:6px">${confidenceNote(m.days, m.n)}</div>`;
  box.appendChild(head);

  // 一、三个数
  const c1 = el('div','card');
  c1.innerHTML = `<h3 style="margin:0 0 8px;font-size:16px">一、三个数</h3>
    <table>
      <tr><td>阅读中位</td><td class="num"><b>${m.read_med}</b></td><td class="num note">p90 ${m.read_p90}</td></tr>
      <tr><td>推荐占全部阅读</td><td class="num"><b>${(m.rec_share*100).toFixed(0)}%</b></td><td class="num note">公众号消息 ${(m.msg_share*100).toFixed(0)}%</td></tr>
      <tr><td>进推荐池</td><td class="num"><b>${m.hit_n}/${m.n} = ${(m.hit_rate*100).toFixed(0)}%</b></td><td class="num note">剔除断供窗后 ${m.hit_n_clean}/${m.blackout.n_clean} = ${(m.hit_rate_clean*100).toFixed(0)}%</td></tr>
      <tr><td>中间带</td><td class="num"><b>${m.mid_n}/${m.n} = ${(m.mid*100).toFixed(0)}%</b></td><td class="num note">推荐阅读 ${MID_LO}–${HIT_REC-1}</td></tr>
      ${m.fin_n? `<tr><td>完读率中位</td><td class="num"><b>${(m.fin_med*100).toFixed(1)}%</b></td><td class="num note">n=${m.fin_n}</td></tr>`:''}
    </table>`;
  box.appendChild(c1);

  // 零、断供
  const c0 = el('div','card');
  const bo = m.blackout;
  if (bo.windows.length){
    c0.innerHTML = `<h3 style="margin:0 0 8px;font-size:16px">二、先看有没有断供 <span class="tag t-warn">有</span></h3>` +
      bo.windows.map(w=>`<div>• 检测到断供窗 <b>${w.from} ~ ${w.to}</b>（${w.days} 天）：该窗口内每天 ≥2 篇发布，但推荐阅读全部 &lt;${BLACKOUT_MAX_REC}。</div>`).join('') +
      `<div style="margin-top:8px">这说明期间是<b>分发中断</b>，不是内容变差。以下所有结构判断都已<b>剔除断供期</b>（剔除 ${bo.n_black} 篇，剩 ${bo.n_clean} 篇可比样本）——否则会把中断期算成你的常态。</div>`;
  } else {
    c0.innerHTML = `<h3 style="margin:0 0 8px;font-size:16px">二、先看有没有断供 <span class="tag t-ok">无</span></h3>
      <div class="note">未检测到断供窗（没有出现「整天 ≥2 篇且推荐全 &lt;${BLACKOUT_MAX_REC}」的连续日期），各期可直接比较。</div>`;
  }
  box.appendChild(c0);

  // 三、内容系列（核心）
  const fam = m.family;
  const c3 = el('div','card');
  if (fam.verdict==='FAMILY_SPARK' || fam.verdict==='FAMILY_SPLIT'){
    c3.innerHTML = `<h3 style="margin:0 0 8px;font-size:16px">三、内容系列：哪条值得继续做，哪条该停
      <span class="tag ${fam.verdict==='FAMILY_SPARK'?'t-warn':'t-bad'}">${fam.verdict==='FAMILY_SPARK'?'家族有孤峰':'家族分化'}</span></h3>
      <div>${fam.headline}</div>
      <div style="margin-top:10px"><b>具体动作</b></div>
      <ol>${fam.actions.map(a=>`<li>${a}</li>`).join('')}</ol>
      <div class="note" style="margin-top:8px">⚠ ${fam.caveats.join('；')}</div>`;
  } else {
    c3.innerHTML = `<h3 style="margin:0 0 8px;font-size:16px">三、内容系列</h3>
      <div class="note">${fam.headline||'样本还不够做系列级判断（每个系列至少 2 篇、且两个系列之间要有 2 倍以上差距）。把导出时间拉长到 1–2 个月再跑一次。'}</div>`;
  }
  box.appendChild(c3);

  // 四、吃到 vs 没吃到
  const g = m.groups;
  const c4 = el('div','card');
  c4.innerHTML = `<h3 style="margin:0 0 8px;font-size:16px">四、吃到推荐的和没吃到的，差在哪</h3>
    <table><tr><th>分组</th><th class="num">n</th><th class="num">阅读中位</th><th class="num">推荐中位</th><th class="num">消息中位</th><th class="num">完读中位</th></tr>
    <tr><td>吃到推荐</td><td class="num">${g.hit.n}</td><td class="num">${g.hit.read_med}</td><td class="num">${g.hit.rec_med}</td><td class="num">${g.hit.msg_med}</td><td class="num">${g.hit.fin_med!==null?(g.hit.fin_med*100).toFixed(1)+'%':'—'}</td></tr>
    <tr><td>没吃到推荐</td><td class="num">${g.miss.n}</td><td class="num">${g.miss.read_med}</td><td class="num">${g.miss.rec_med}</td><td class="num">${g.miss.msg_med}</td><td class="num">${g.miss.fin_med!==null?(g.miss.fin_med*100).toFixed(1)+'%':'—'}</td></tr>
    </table>
    <div class="note">吃到的组在阅读中位上高 ${(g.hit.read_med-g.miss.read_med).toFixed(0)}，
    消息渠道中位差 ${(g.hit.msg_med-g.miss.msg_med).toFixed(0)} ——
    ${g.hit.msg_med < g.miss.msg_med ? '说明差距不是粉丝打开造成的。' : '消息渠道没差多少，差距在分发侧。'}</div>`;
  box.appendChild(c4);

  // 五、逐篇定位
  const byRec = [...m.rows].sort((a,b)=>(b.rec||0)-(a.rec||0)).slice(0,3);
  const byRead = [...m.rows].sort((a,b)=>(a.read||0)-(b.read||0)).slice(0,5);
  const c5 = el('div','card');
  c5.innerHTML = `<h3 style="margin:0 0 8px;font-size:16px">五、逐篇定位</h3>
    <div class="note">推荐最高的 3 篇（结构可复用）</div>
    <table><tr><th>日期</th><th>标题</th><th class="num">阅读</th><th class="num">推荐</th><th class="num">消息</th></tr>
    ${byRec.map(r=>`<tr><td>${r.date}</td><td>${r.title}</td><td class="num">${r.read}</td><td class="num">${r.rec}</td><td class="num">${r.msg}</td></tr>`).join('')}</table>
    <div class="note" style="margin-top:10px">阅读最低的 5 篇（量丢在这里）</div>
    <table><tr><th>日期</th><th>标题</th><th class="num">阅读</th><th class="num">推荐</th><th class="num">消息</th></tr>
    ${byRead.map(r=>`<tr><td>${r.date}</td><td>${r.title}</td><td class="num">${r.read}</td><td class="num">${r.rec}</td><td class="num">${r.msg}</td></tr>`).join('')}</table>`;
  box.appendChild(c5);

  // 数据完整性
  if (skipped && skipped.length){
    const cs = el('div','card');
    cs.innerHTML = `<h3 style="margin:0 0 8px;font-size:16px">六、数据完整性说明 <span class="tag t-warn">${skipped.length} 行未进分析</span></h3>
      <div>你发来的表格里有 ${skipped.length} 行没能进入分析，原因如下（不影响上面结论的成立，但你应该知道哪些内容没被算进去）：</div>
      <ul>${skipped.slice(0,10).map(s=>`<li><b>${s.title}</b> —— ${s.reason}</li>`).join('')}</ul>`;
    box.appendChild(cs);
  }

  box.appendChild(el('div','note',
    `列映射：${Object.entries(mapping).map(([k,v])=>`${k}←${v}`).join('，')}`));

  out.innerHTML = ''; out.appendChild(box);
  $('#cta').style.display = 'block';
}

/* ============================================================
 * 文件处理
 * ============================================================ */
async function handleFile(file){
  const err = $('#err'); err.classList.add('hide');
  try{
    let rows;
    const nm = file.name.toLowerCase();
    if (nm.endsWith('.csv')){
      rows = parseCSV(await file.text());
    } else {
      rows = await readXlsx(await file.arrayBuffer());
    }
    const {arts, skipped, mapping} = convert(rows);
    if (!arts.length) throw new Error('一行有效数据都没有 —— 表头可能不对，或读数列是空的。');
    if (arts.length < FIN_MIN_ROWS) throw new Error(
      `有效数据只有 ${arts.length} 篇（<${FIN_MIN_ROWS}），样本不够给结论。请把导出范围拉长（至少 10 篇、覆盖 7 个发布日）。`);
    const days = new Set(arts.map(a=>a.date)).size;
    if (days < FIN_MIN_DAYS) throw new Error(
      `覆盖天数 ${days} < ${FIN_MIN_DAYS}，窗口太短不可判。请把导出时间范围拉长到至少 1 个完整自然周。`);
    const m = analyse(arts);
    m.rows = arts;
    render(m, skipped, mapping);
  } catch(e){
    err.textContent = '出错了：' + (e && e.message ? e.message : e);
    err.classList.remove('hide');
    $('#out').classList.add('hide');
  }
}
const drop = $('#drop'), fileInput = $('#file');
drop.addEventListener('click', ()=>fileInput.click());
$('#pick').addEventListener('click', e=>{ e.stopPropagation(); fileInput.click(); });
fileInput.addEventListener('change', e=>{ if (e.target.files[0]) handleFile(e.target.files[0]); });
['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev, e=>{ e.preventDefault(); drop.classList.add('hot'); }));
['dragleave','drop'].forEach(ev=>drop.addEventListener(ev, e=>{ e.preventDefault(); drop.classList.remove('hot'); }));
drop.addEventListener('drop', e=>{ const f=e.dataTransfer.files[0]; if(f) handleFile(f); });