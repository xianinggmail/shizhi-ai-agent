'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ratings = {mastered:'已掌握',unsure:'不确定',unlearned:'未掌握'};
let config, books = [], selected = null, current = null, stats = null;
let draftTimer, pendingDraft = Promise.resolve(), timer, watchedJob=null;
let dashboardTab='overview';
let pendingUploadBook=null;
const safe = fn => async (...args) => { try { await fn(...args); } catch(e) { message(e.message || '操作失败，请重试。'); } };
function message(text) { $('message').textContent = text; $('message').hidden = !text; }
async function api(url, data) {
  const options = data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Study-Token':config.token},body:JSON.stringify(data)};
  let response;
  try { response = await fetch(url, options); } catch { throw Error('无法连接本机服务。请检查启动窗口，并重试；当前内容尚未确认保存。'); }
  const result = await response.json();
  if (!response.ok) throw Error(result.error || '操作失败');
  return result;
}
function view(name) {
  for (const id of ['dashboard','study','reviewView','paperView']) $(id).hidden = id !== name;
  $('overview').hidden=dashboardTab!=='overview';
  $('libraryArea').hidden=dashboardTab!=='library';
  $('homeNav').classList.toggle('active', name==='dashboard'&&dashboardTab==='overview');
  $('libraryNav').classList.toggle('active', name==='study'||(name==='dashboard'&&dashboardTab==='library'));
  $('reviewNav').classList.toggle('active', name === 'reviewView');
  $('papersNav').classList.toggle('active',name==='paperView');
  for(const id of ['homeNav','libraryNav','reviewNav','papersNav']) {
    if($(id).classList.contains('active'))$(id).setAttribute('aria-current','page');
    else $(id).removeAttribute('aria-current');
  }
  $('breadcrumb').textContent = {dashboard:dashboardTab==='overview'?'学习总览':'学习卡片',study:'回忆与核对',reviewView:'复习中心',paperView:'老师论文精读'}[name];
  message('');
  if (name !== 'study') clearInterval(timer);
  window.scrollTo(0,0);
}

function renderMode() {
  const online=config.mode==='deepseek';
  config.needs_restart=online&&!config.local_recheck;
  document.querySelector('.banner').innerHTML=online
    ? '<span class="mode-pill">DeepSeek '+(config.ready?'已配置':'待配置')+'</span><span>模型 '+esc(config.model)+' · 仅发送所选页面 · API费用不设上限 · 历史演示卡片仍单独标注。'+(config.needs_restart?'更新已安装，请双击本机启动并配置.cmd重启后使用新版核验。':config.ready?'重点为模型建议，非考试预测；支持免费本地重新核验。':esc(config.error || '请在本机运行“配置DeepSeek.cmd”，完成后点击重新检查。'))+'</span><button class="text-button" id="recheckAI">重新检查</button>'
    : '<span class="mode-pill">离线演示</span><span>原文摘录与模板问题，未调用AI。无API费用。</span>';
  const banner=document.querySelector('.banner');
  banner.classList.toggle('ai-banner',online);
  banner.classList.remove('banner-expanded');
  if(online){
    const description=banner.querySelector('span:nth-child(2)');
    description.id='aiUsageDetails';
    const toggle=document.createElement('button');
    toggle.className='text-button banner-toggle';toggle.type='button';
    toggle.textContent='使用说明';toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls','aiUsageDetails');
    toggle.onclick=()=>{const expanded=banner.classList.toggle('banner-expanded');toggle.setAttribute('aria-expanded',String(expanded));toggle.textContent=expanded?'收起说明':'使用说明';};
    banner.append(toggle);
  }
  if($('recheckAI'))$('recheckAI').onclick=safe(async()=>{await refresh();selectBook(selected);message(config.needs_restart?'更新文件已安装，但当前服务尚未重启；请双击本机启动并配置.cmd。':config.ready?'本机凭据可读取；首次生成时将验证API是否可用。':'检查结果：'+config.error);});
  $('connectionTitle').textContent=online?(config.ready?'DeepSeek 已配置':'DeepSeek 待配置'):'离线演示模式';
  $('connectionDetail').textContent=online?'仅在你发起生成时调用。累计请求 '+Number(config.calls||0)+' 次；凭据可读取不代表 API 已通过验证。':'使用原文摘录与模板问题，不调用模型，不产生 API 费用。';
}
async function generateTask(payload) {
  if(config.needs_restart)throw Error('请先双击本机启动并配置.cmd重启服务；本次没有发起生成请求。');
  const job=await api('/api/jobs',payload);
  await watchJob(job.id,payload);
}
async function watchJob(jid,payload) {
  dashboardTab='library';
  view('dashboard');watchedJob=jid;
  const progress=$('uploadProgress');progress.hidden=false;
  while(watchedJob===jid) {
    const job=await api('/api/jobs/'+jid);
    if(watchedJob!==jid)return;
    progress.textContent=job.message || '正在生成…';
    if(job.status==='ready') {
      watchedJob=null;progress.textContent='知识点已生成并核验，结果已保存在本机。';
      await loadSession(job.session_id);return;
    }
    if(job.status==='failed') {
      watchedJob=null;
      const request=payload || job.request;
      progress.innerHTML='<strong>生成未完成</strong><p>'+esc(job.error)+'</p>'+diagnosticsHTML(job.diagnostics)+(request?(job.can_recheck?'<button class="button secondary" id="recheckGeneration">重新核验本地结果 · 不调用AI</button> ':'')+'<button class="button secondary" id="retryGeneration">重新请求DeepSeek · 可能收费</button>':'请返回所选范围重新发起。');
      if($('recheckGeneration'))$('recheckGeneration').onclick=safe(()=>generateTask({...request,local_only:true,retry_ai:false}));
      if($('retryGeneration'))$('retryGeneration').onclick=safe(async()=>{
        if(confirm('将重新向DeepSeek请求，可能再次计费。已有可核验响应会先在本地检查。确认重新请求？'))await generateTask({...request,retry_ai:true,local_only:false});
      });
      await refresh();return;
    }
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
}

async function refresh() {
  config=await api('/api/config');renderMode();
  [books,stats] = await Promise.all([api('/api/books'),api('/api/stats')]);
  $('reviewBadge').textContent = stats.review;
  $('reviewTotal').textContent=stats.review+' 条待复习';
  $('libraryBadge').textContent=books.length;
  renderOverview();
  if(typeof renderPaperPicker==='function')renderPaperPicker();
  renderBooks();
  if(pendingUploadBook){
    const uploaded=books.find(b=>b.id===pendingUploadBook);
    if(uploaded&&['ready','failed'].includes(uploaded.status)){
      $('uploadProgress').textContent=uploaded.status==='ready'?'《'+uploaded.title+'》已解析完成。选择章节或页码，即可开始学习。':'《'+uploaded.title+'》解析失败，请查看材料详情，检查文件后重试。';
      pendingUploadBook=null;
    }
  }
  $('resume').innerHTML = stats.active.map(s => `<div class="resume"><div><strong>继续上次的学习</strong><p>${esc(s.title)} · ${s.done} / ${s.total} 条已完成</p></div><button class="button secondary" data-resume="${s.id}">继续学习 →</button></div>`).join('');
  const jobs=await api('/api/jobs');
  $('resume').innerHTML+=jobs.map(j=>`<div class="resume"><div><strong>生成任务仍在后台运行</strong><p>${esc(j.title)} · ${esc(j.message)}</p></div><button class="button secondary" data-job="${j.id}">查看生成状态</button></div>`).join('');
  $('resume').querySelectorAll('[data-job]').forEach(b=>b.onclick=safe(()=>watchJob(b.dataset.job)));
  $('resume').querySelectorAll('[data-resume]').forEach(b => b.onclick = safe(() => loadSession(b.dataset.resume)));
}
function openBook(id){return showLibrary(id);}
function renderOverview(){
  renderPaperOverview();
  const ready=books.find(b=>b.status==='ready'),active=stats.active[0];
  const percent=stats.learned?Math.round(stats.mastered/stats.learned*100):0;
  $('masteryPercent').textContent=stats.learned?percent+'%':'—';
  $('masteryRing').style.setProperty('--progress',percent+'%');
  $('masteryRing').classList.toggle('has-data',!!stats.learned);
  $('masteryNote').textContent=stats.learned?'在 '+stats.learned+' 个已自评知识点中，'+stats.mastered+' 个自评已掌握。不是阅读完成率。':'完成一次自评，开始记录积累。';
  $('railMastered').textContent=stats.mastered;$('railReview').textContent=stats.review;
  $('nextStep').textContent=active?'继续上次学习 ↗':ready?'选择范围，开始学习 ↗':'上传第一份资料 ↗';
  $('nextStep').onclick=safe(()=>active?loadSession(active.id):ready?showLibrary(ready.id):$('pdfFile').click());
  $('quickResumeText').textContent=active?'上次还有 '+(active.total-active.done)+' 个知识点，点击继续。':'从一道问题开始，先回忆再看答案。';
  $('quickResume').onclick=safe(()=>active?loadSession(active.id):showLibrary(ready?.id));
  const recent=books.slice(0,3);
  $('recentBooks').innerHTML=recent.length?recent.map(b=>`<button class="recent-book" data-open-book="${b.id}"><span class="recent-icon" aria-hidden="true">PDF</span><span><strong>${esc(b.title)}</strong><small>${b.total||'—'} 页 · 已自评 ${b.learned} 条</small></span><span class="status-pill ${b.status==='ready'?'ready':b.status==='failed'?'failed':''}">${statusText(b)}</span><span class="recent-arrow" aria-hidden="true">↗</span></button>`).join(''):'<div class="empty-book"><strong>你的书架还是空的</strong><p>上传第一本文字版 PDF，开始积累。</p><button class="button secondary" id="emptyUpload">＋ 上传 PDF</button></div>';
  $('recentBooks').querySelectorAll('[data-open-book]').forEach(b=>b.onclick=safe(()=>openBook(b.dataset.openBook)));
  if($('emptyUpload'))$('emptyUpload').onclick=()=>$('pdfFile').click();
}
function renderPaperOverview(){
 const summary=stats.paper_learning;
 for(const [id,key] of [['paperReadCount','analyzed_papers'],['paperQuestionCount','generated_questions'],['paperDraftCount','drafted_questions'],['paperViewedCount','viewed_answers']])$(id).textContent=summary?summary[key]:'—';
 $('paperOverviewStatus').textContent=summary?'已完成 '+summary.ready_batches+' 批 · 处理中 '+summary.running_batches+' 批 · 需处理 '+summary.failed_batches+' 批。草稿与查看答案分别记录，不计为掌握。':'后台需要重启后才能读取论文学习统计，原有记录仍保存在本机。';
 $('overviewPaperRecords').innerHTML=!summary?'<p class="muted">重启后台后可显示精读记录与练习情况。</p>':summary.recent.length?summary.recent.map(job=>'<button class="recent-book" data-overview-paper="'+job.id+'"><span class="recent-icon" aria-hidden="true">✧</span><span><strong>'+esc(job.teacher)+' · '+esc(job.titles.join(' / '))+'</strong><small>'+job.questions+' 道题 · '+job.drafted+' 道有草稿 · '+job.viewed+' 道已查看答案</small></span><span class="status-pill">'+esc({ready:'已生成',failed:'需处理',running:'精读中',queued:'排队中'}[job.status]||job.status)+'</span><span class="recent-arrow" aria-hidden="true">↗</span></button>').join(''):'<div class="empty-book"><strong>还没有精读记录</strong><p>进入论文精读，选择材料后开始；查看总览不会调用 AI。</p></div>';
 $('overviewPaperRecords').querySelectorAll('[data-overview-paper]').forEach(button=>button.onclick=safe(async()=>{await showPapers();await openPaperJob(button.dataset.overviewPaper);if(!$('paperResult').hidden)$('paperResult').scrollIntoView({block:'start'});}));
}
function statusText(b){return b.status==='ready'?'可学习':b.status==='failed'?'处理失败':b.status==='queued'?'等待处理':`提取中 ${b.processed}/${b.total||'—'}`;}
function renderBooks() {
  const query=$('bookSearch').value.trim().toLocaleLowerCase('zh-CN'),filter=$('bookFilter').value;
  const visible=books.filter(b=>(!query||b.title.toLocaleLowerCase('zh-CN').includes(query))&&(filter==='all'||(filter==='processing'?['processing','queued'].includes(b.status):b.status===filter)));
  const sort=$('bookSort').value;
  if(sort==='title')visible.sort((a,b)=>a.title.localeCompare(b.title,'zh-CN'));
  if(sort==='learned')visible.sort((a,b)=>b.learned-a.learned);
  $('libraryResult').textContent=`显示 ${visible.length} / ${books.length} 份资料`;
  $('bookList').innerHTML = visible.length ? visible.map(b => `<article class="book ${b.id===selected?'selected':''}"><button class="book-select" data-book="${b.id}" aria-pressed="${b.id===selected}"><span class="book-icon">PDF</span><span><span class="book-title">${esc(b.title)}</span><span class="book-meta">${b.total || '—'} 页 · 已自评 ${b.learned} 条</span><span class="book-state ${b.status==='failed'?'failed':''}">${statusText(b)}</span></span><span class="book-chevron" aria-hidden="true">↗</span></button><div class="actions"><a class="text-button" href="/api/books/${b.id}/pdf" target="_blank" rel="noopener">打开 PDF ↗</a>${b.status==='failed'?`<button class="text-button" data-retry="${b.id}">重试解析</button>`:''}<button class="text-button delete-button" data-delete="${b.id}">删除材料</button></div></article>`).join('') : `<div class="empty-book"><strong>${books.length?'没有匹配的资料':'你的书架还是空的'}</strong><p>${books.length?'试试其他名称，或清除状态筛选。':'上传第一本文字版 PDF，开始积累。'}</p>${books.length?'<button class="button secondary" id="clearBookFilters">清除搜索与筛选</button>':''}</div>`;
  if($('clearBookFilters'))$('clearBookFilters').onclick=()=>{$('bookSearch').value='';$('bookFilter').value='all';renderBooks();};
  $('bookList').querySelectorAll('[data-book]').forEach(b => b.onclick = () => selectBook(b.dataset.book));
  $('bookList').querySelectorAll('[data-retry]').forEach(b => b.onclick = safe(async () => {await api(`/api/books/${b.dataset.retry}/retry`,{}); await refresh(); selectBook(b.dataset.retry);}));
  $('bookList').querySelectorAll('[data-delete]').forEach(b => b.onclick = safe(async () => {
    const book = books.find(x => x.id === b.dataset.delete);
    if (!confirm(`删除《${book.title}》？\n将同时删除 PDF、提取文本、卡片、学习任务、回忆草稿、全部掌握记录，以及涉及这份材料的整批论文精读与题目，无法撤销。`)) return;
    await api(`/api/books/${book.id}/delete`,{});
    if (selected===book.id) selected=null;
    await refresh();
    selectBook(selected || books[0]?.id);
  }));
}
function selectBook(id) {
  selected=id || null;
  renderBooks();
  const b=books.find(x=>x.id===id);
  if (!b) { $('rangePanel').innerHTML='<div class="empty-range"><span>▤</span><h3>从一本参考书开始</h3><p>上传可提取文字的 PDF，选择要学习的内容。</p><small>最多 30 MB / 1000 页 · 暂不支持 OCR</small></div>';return; }
  if (b.status!=='ready') {
    $('rangePanel').innerHTML=`<h2>${esc(b.title)}</h2><div class="notice">${b.status==='failed'?esc(b.error):`正在处理：${b.processed} / ${b.total || '—'} 页，请稍候。可以离开此页面。`}</div>${b.status==='failed'?'':'<progress max="'+(b.total||1)+'" value="'+b.processed+'"></progress>'}`;
    return;
  }
  $('rangePanel').innerHTML=`<div class="card-tag">01 / 选择学习范围</div><h3>${esc(b.title)}</h3><p class="muted">已提取 ${b.readable_pages} / ${b.total} 页文字。书名取自文件名，请核对材料。</p>${b.warnings.map(w=>`<div class="notice">${esc(w)}</div>`).join('')}
  <label class="label" for="chapter">章节目录</label><select id="chapter"><option value="">${b.chapters.length?'手动选择页码，或选择章节':'未发现 PDF 书签目录，请使用页码范围'}</option>${b.chapters.map((c,i)=>`<option value="${i}">${esc(c.title)}（${c.start}—${c.end} 页）</option>`).join('')}</select>
  <div class="notice info">目录来自 PDF 书签，章节边界仅供参考，可手动修正。以下均为 PDF 物理页码，可能与书上印刷页码不同。</div>
  <div class="fields"><label><span class="label">起始页</span><input id="startPage" aria-label="起始页" type="number" min="1" max="${b.total}" value="1"></label><span>—</span><label><span class="label">结束页</span><input id="endPage" aria-label="结束页" type="number" min="1" max="${b.total}" value="${Math.min(5,b.total)}"></label></div>
  <button class="text-button" id="previewPage">查看起始页原文 ↗</button>
  <label class="label" for="cardCount">本次安排</label><select id="cardCount"><option value="3">3 个知识点 · 少量专注</option><option value="4" selected>4 个知识点 · 约 10 分钟</option><option value="5">5 个知识点 · 多学一点</option></select>
  <div class="range-footer"><span>${config.mode==='deepseek'?'AI提炼核心概念并提供学习理由。':'演示将原文分段摘录；不判断重点。'}<br>尾批不足3条时按实际数量安排。</span><button class="button primary" id="startStudy" ${config.ready&&!config.needs_restart?'':'disabled'}>${config.mode==='deepseek'?'提炼重点并学习 →':'开始学习 →'}</button></div>`;
  if(config.mode==='deepseek'&&!config.ready)$('rangePanel').insertAdjacentHTML('beforeend','<div class="notice">'+esc(config.error)+'</div>');
  $('chapter').onchange=()=>{const c=b.chapters[$('chapter').value];if(c){$('startPage').value=c.start;$('endPage').value=c.end;}};
  $('previewPage').onclick=safe(()=>source(b.id,Number($('startPage').value)));
  $('startStudy').onclick=safe(async()=>{
    const button=$('startStudy');button.disabled=true;
    try {await generateTask({book_id:b.id,start:Number($('startPage').value),end:Number($('endPage').value),count:Number($('cardCount').value),kind:'new'});}
    finally {if(button.isConnected)button.disabled=false;}
  });
}
async function source(bid,page) {
  const result=await api(`/api/books/${bid}/pages/${page}`);
  $('sourceTitle').textContent=`PDF 第 ${page} 页 · 原文核对`;
  $('sourceText').textContent=result.text || '此页没有提取到文字。请打开 PDF 查看。';
  $('pdfLink').href=`/api/books/${bid}/pdf#page=${page}`;
  $('sourceDialog').showModal();
}
function queueDraft() {
  const input=$('recallInput');
  if(!current || !input) return pendingDraft;
  const item=current.items.find(i=>!i.rating);
  if(!item)return pendingDraft;
  const sid=current.id,cid=item.card_id,draft=input.value;
  $('saveStatus').textContent='正在保存…';
  pendingDraft=pendingDraft.catch(()=>{}).then(()=>api(`/api/sessions/${sid}/items/${cid}`,{action:'draft',draft})).then(()=>{if($('saveStatus'))$('saveStatus').textContent='草稿已保存到本机';});
  return pendingDraft;
}
async function flushDraft() {clearTimeout(draftTimer);await queueDraft();}
async function loadSession(id) {if(typeof flushPaperDrafts==='function')await flushPaperDrafts();
  current=await api(`/api/sessions/${id}`);
  view('study');renderSession();
}
function diagnosticsHTML(report) {
  const issues=report?.issues || [];
  if(!issues.length)return '';
  return '<details><summary>查看具体原因（'+issues.length+'项）</summary><ul>'+issues.slice(0,30).map(i=>'<li>'+esc((i.card?'第'+i.card+'条：':'')+i.message)+'</li>').join('')+'</ul></details>';
}
function generationNotice(session) {
  const report=session.generation_report;
  if(session.kind!=='new'||!report||(!report.rejected&&!report.restored_quotes&&!report.rechecked_locally))return '';
  return '<div class="notice info">已保留 '+Number(report.accepted||0)+' 条可核验知识点'+(report.rejected?'，跳过 '+Number(report.rejected)+' 条不合格内容':'')+'。'+(report.restored_quotes?'已将空格、换行差异恢复为PDF原文。':'')+(report.rechecked_locally?'本次使用本地响应重新核验，未新增模型调用。':'')+diagnosticsHTML(report)+'</div>';
}

function renderSession() {
  clearInterval(timer);
  const s=current,item=s.items.find(i=>!i.rating),done=s.items.filter(i=>i.rating).length;
  if(!item) {
    $('study').innerHTML=`<div class="study-wrap"><div class="hero"><div><div class="eyebrow">每一点积累，都值得看见</div><h1>这一轮，完成了。</h1><p>${esc(s.book_title)} · 共 ${s.items.length} 条</p></div></div><div class="panel"><h2>本次自评记录</h2>${s.items.map(i=>`<div class="summary-list"><span>${esc(i.title)}</span><strong>${ratings[i.rating]}</strong></div>`).join('')}<div class="notice info">${s.items.filter(i=>i.rating!=='mastered').length} 条加入待复习。自评不等同于无提示正确回忆；当前版本不自动判断回答是否正确。</div><p class="muted">任务自开始至完成经过 ${Math.max(1,Math.round((s.completed-s.created)/60))} 分钟（包含离开页面的时间），不代表有效学习时长。</p><button class="button primary" id="finishHome">返回学习工作台</button> <button class="button secondary" id="finishReview">查看待复习</button></div></div>`;
    $('finishHome').onclick=safe(home);$('finishReview').onclick=safe(showReview);refresh().catch(e=>message(e.message));return;
  }
  $('study').innerHTML=`<div class="study-wrap"><div class="study-top"><button class="text-button" id="backHome">← 保存并返回</button><span class="muted">${esc(s.book_title)} · ${s.kind==='review'?'复习任务':'新学习任务'}</span><div><label for="fontSize" class="muted">字号 </label><select id="fontSize" class="font-select"><option value="16">小</option><option value="18" selected>标准</option><option value="22">大</option></select></div></div><div class="row"><progress max="${s.items.length}" value="${done}"></progress><span class="muted">${done}/${s.items.length}</span></div>
  ${generationNotice(s)}
  ${s.items.length<3?'<div class="notice">这是尾批，本次按实际剩余数量安排 '+s.items.length+' 条。</div>':''}
  <article class="panel study-card"><div class="card-tag">${s.kind==='review'?'再想一遍':'先回忆，再核对'} · ${done+1} / ${s.items.length} · ${item.mode==='ai'?'AI提炼 / '+esc(item.model):'离线演示卡片'}</div><h2>${esc(item.title)}</h2><div class="question">${esc(item.question)}</div><label class="label" for="recallInput">写下你的回忆（可选，也可以口头复述）</label><textarea id="recallInput" maxlength="6000" placeholder="先试着用自己的话说一说……">${esc(item.draft)}</textarea><div class="muted" id="saveStatus" role="status">草稿保存在本机；自动保存或点击“保存并返回”。</div>
  ${!item.revealed?'<button class="button primary reveal-button" id="reveal">我已尝试回忆，查看答案</button>':`<section class="answer"><div class="card-tag">${item.mode==='ai'?'参考答案 · AI解释，需结合引文核对':'参考答案 · 离线原文摘录，非AI解释'}</div><div class="answer-text">${esc(item.answer)}</div><div class="notice info">${item.mode==='ai'?'学习优先级：'+({1:'先学',2:'随后',3:'补充'}[item.priority]||'建议')+'（非考试概率）<br>学习理由（AI建议）：'+esc(item.reason)+'<br>记忆提示（AI生成）：'+esc(item.hint):'记忆提示（固定模板）：用自己的话复述观点，再核对关键术语；片段可能截断完整论证。'}</div><div class="quote"><strong>可核对的原文出处</strong><br>${esc(s.book_title)} · PDF 第 ${item.page} 页<br>${esc(item.quote)}</div><button class="text-button" id="checkSource">查看这一页的完整提取文本与 PDF ↗</button><label class="label">核对后，你觉得掌握得怎么样？</label><div class="rating-row"><button class="button secondary" data-rating="unlearned">还未掌握</button><button class="button secondary" data-rating="unsure">不太确定</button><button class="button primary" data-rating="mastered">已掌握</button></div></section>`}</article><div class="study-bottom"><span>目标约 10 分钟，不强制结束</span><span id="elapsed"></span></div></div>`;
  $('backHome').onclick=safe(async()=>{await flushDraft();await home();});
  $('recallInput').oninput=()=>{clearTimeout(draftTimer);$('saveStatus').textContent='有未保存的修改…';draftTimer=setTimeout(()=>queueDraft().catch(e=>message(e.message)),350);};
  $('fontSize').onchange=()=>document.documentElement.style.setProperty('--font',$('fontSize').value+'px');
  if($('reveal'))$('reveal').onclick=safe(async()=>{ $('reveal').disabled=true;try {await flushDraft();await api(`/api/sessions/${s.id}/items/${item.card_id}`,{action:'reveal'});await loadSession(s.id);}catch(e){if($('reveal'))$('reveal').disabled=false;throw e;}});
  if($('checkSource'))$('checkSource').onclick=safe(()=>source(s.book_id,item.page));
  document.querySelectorAll('[data-rating]').forEach(b=>b.onclick=safe(async()=>{
    document.querySelectorAll('[data-rating]').forEach(x=>x.disabled=true);
    try {await flushDraft();await api(`/api/sessions/${s.id}/items/${item.card_id}`,{action:'rate',rating:b.dataset.rating});await loadSession(s.id);}
    catch(e){document.querySelectorAll('[data-rating]').forEach(x=>x.disabled=false);throw e;}
  }));
  function tick(){if($('elapsed'))$('elapsed').textContent=`自任务开始 ${Math.floor((Date.now()/1000-s.created)/60)} 分钟（含离开时间）`;}
  tick();timer=setInterval(tick,10000);
}
async function home(){if(typeof flushPaperDrafts==='function')await flushPaperDrafts();watchedJob=null;await flushDraft();current=null;dashboardTab='overview';await refresh();selectBook(selected || books[0]?.id);view('dashboard');}
async function showLibrary(id){if(typeof flushPaperDrafts==='function')await flushPaperDrafts();watchedJob=null;await flushDraft();current=null;dashboardTab='library';await refresh();selectBook(id || selected || books[0]?.id);view('dashboard');}
async function showReview(){if(typeof flushPaperDrafts==='function')await flushPaperDrafts();
  watchedJob=null;
  await flushDraft();current=null;view('reviewView');await refresh();
  const list=await api('/api/review');
  const groups=[...new Set(list.map(x=>x.book_id))];
  $('reviewList').innerHTML=groups.length?groups.map(bid=>{const rows=list.filter(x=>x.book_id===bid);return `<section><div class="section-heading"><div><h3>${esc(rows[0].book_title)}</h3><p>${rows.length} 条待复习</p></div><button class="button primary" data-review="${bid}">开始复习 →</button></div>${rows.map(c=>`<div class="review-row"><span>${esc(c.title)}</span><span class="muted">${ratings[c.rating]}</span></div>`).join('')}</section>`;}).join(''):'<div class="empty-range"><span>✓</span><h3>暂时没有待复习内容</h3><p>学习后标为“不确定”或“未掌握”的内容会出现在这里。</p></div>';
  $('reviewList').querySelectorAll('[data-review]').forEach(b=>b.onclick=safe(async()=>{b.disabled=true;try{const s=await api('/api/sessions',{book_id:b.dataset.review,kind:'review',count:4});await loadSession(s.id);if(s.resumed)message('先继续这本书的未完成任务，完成后再安排复习。');}finally{if(b.isConnected)b.disabled=false;}}));
}
async function upload(file){
  if(!file)return;
  if(!file.name.toLowerCase().endsWith('.pdf'))throw Error('请选择 PDF 文件。');
  if(file.size>30*1024*1024)throw Error('文件超过 30 MB，请拆分后上传。');
  await showLibrary();
  message('');$('uploadProgress').hidden=false;$('uploadProgress').textContent='正在上传…';$('pdfFile').disabled=true;
  try {
    const book=await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open('POST','/api/books');xhr.setRequestHeader('X-Study-Token',config.token);xhr.setRequestHeader('X-Filename',encodeURIComponent(file.name));xhr.setRequestHeader('Content-Type','application/pdf');xhr.timeout=60000;
      xhr.upload.onprogress=e=>{if(e.lengthComputable)$('uploadProgress').textContent=`上传中 ${Math.round(e.loaded/e.total*100)}% · 请勿关闭页面`;};
      xhr.onload=()=>{try{const data=JSON.parse(xhr.responseText);xhr.status<300?resolve(data):reject(Error(data.error));}catch{reject(Error('上传响应异常，请重试。'));}};
      xhr.onerror=()=>reject(Error('上传失败，请检查本机服务。'));xhr.ontimeout=()=>reject(Error('上传超时，请重试。'));xhr.send(file);
    });
    pendingUploadBook=book.id;
    $('uploadProgress').textContent='上传完成，正在解析 PDF。处理状态见参考书列表。';$('bookSearch').value='';$('bookFilter').value='all';await showLibrary(book.id);
  } catch(e){$('uploadProgress').textContent='上传未完成：'+e.message;throw e;}
  finally{$('pdfFile').disabled=false;$('pdfFile').value='';}
}
$('overviewPapers').onclick=safe(()=>showPapers());$('overviewAllPapers').onclick=safe(()=>showPapers());
$('homeNav').onclick=safe(home);$('libraryNav').onclick=safe(()=>showLibrary());$('reviewNav').onclick=safe(showReview);
$('quickLibrary').onclick=safe(()=>showLibrary());$('allBooks').onclick=safe(()=>showLibrary());
$('quickReview').onclick=safe(showReview);$('railReviewButton').onclick=safe(showReview);
$('uploadButton').onclick=()=>$('pdfFile').click();
$('bookSearch').oninput=renderBooks;$('bookFilter').onchange=renderBooks;$('bookSort').onchange=renderBooks;
$('connectionRefresh').onclick=safe(async()=>{await refresh();message('本机服务连接正常。'+(config.mode==='deepseek'?'当前仅检查配置，未发起付费模型请求。':'当前为离线演示模式。'));});
$('todayLabel').textContent=new Intl.DateTimeFormat('zh-CN',{month:'long',day:'numeric',weekday:'long'}).format(new Date());
$('overview').querySelector('.focus-card').after($('resume'));
$('pdfFile').onchange=safe(()=>upload($('pdfFile').files[0]));
$('privacyBtn').onclick=()=>{ $('dataPath').textContent='当前数据目录：'+(config?.data_dir||'未连接');$('privacyDialog').showModal();};
$('closePrivacy').onclick=()=>$('privacyDialog').close();$('closeSource').onclick=()=>$('sourceDialog').close();
window.addEventListener('beforeunload',()=>{
  const input=$('recallInput'),item=current?.items.find(i=>!i.rating);
  if(input&&item&&config)fetch(`/api/sessions/${current.id}/items/${item.card_id}`,{method:'POST',headers:{'Content-Type':'application/json','X-Study-Token':config.token},body:JSON.stringify({action:'draft',draft:input.value}),keepalive:true}).catch(()=>{});
});
safe(async()=>{config=await api('/api/config');await refresh();selectBook(books[0]?.id);view('dashboard');setInterval(async()=>{
  if(!books.some(b=>['queued','processing'].includes(b.status)))return;
  try {const old=books.find(x=>x.id===selected);await refresh();const updated=books.find(x=>x.id===selected);if(!$('dashboard').hidden && (old?.status!==updated?.status || updated?.status!=='ready'))selectBook(selected);}catch(e){message(e.message);}
},1000);})();
