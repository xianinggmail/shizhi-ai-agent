'use strict';
let paperSelected=new Set(),paperMeta=new Map(),paperJob=null,paperWatch=null,paperTab='analysis',paperBusy=false;
const paperTimers=new Map();let paperPending=Promise.resolve();
const paperAttr={author_claim:'论文作者主张',cited_theory:'论文引用的他人理论',ai_synthesis:'AI 综合分析'};
const paperCategories={question:'研究问题',theory:'研究理论',argument:'核心论点',method:'研究方法',finding:'主要发现',limitation:'研究局限'};
function paperSupported(){return !!config?.paper_lab_version;}
function paperControls(){
 const needsRestart=paperSupported()&&(config.paper_authorship_required!==false||config.paper_essay_style!=='scenario-v3'||config.paper_read_style!=='grounded-v2'||config.paper_review_style!=='detail-v1');
 $('paperGenerate').disabled=!paperSupported()||config.mode!=='deepseek'||!config.ready||paperBusy||needsRestart;
 $('paperPlan').disabled=!paperSupported()||paperBusy||needsRestart;
 $('paperCapability').hidden=paperSupported()&&config.mode==='deepseek'&&config.ready&&!needsRestart;
 $('paperCapability').textContent=!paperSupported()?'当前后台尚未升级。请运行论文功能升级包中的安装并打开.cmd，重启后使用。':needsRestart?'页面已更新，请关闭原启动窗口，再双击本机启动并配置.cmd重启服务。':config.mode!=='deepseek'?'当前为离线演示：可以上传与选择论文，但不会伪造精读、观点或试题。真实生成需要本机配置 DeepSeek。':!config.ready?'DeepSeek 凭据尚不可读取。请在你自己的 Windows 会话运行本机启动并配置.cmd。':'';
}
function renderPaperPicker(){
 if(paperJob&&paperJob.payload.papers.some(p=>!books.some(b=>b.id===p.book_id))){paperJob=null;paperWatch=null;paperBusy=false;$('paperResult').hidden=true;$('paperResult').innerHTML='';$('paperJobStatus').hidden=true;}
 for(const id of paperMeta.keys())if(!books.some(b=>b.id===id))paperMeta.delete(id);
 for(const id of paperSelected)if(!books.some(b=>b.id===id))paperSelected.delete(id);
 $('paperSelectionCount').textContent='已选 '+paperSelected.size+' / 3 篇';
 $('paperPicker').innerHTML=books.length?books.map(b=>{
  return '<article class="paper-choice '+(paperSelected.has(b.id)?'chosen':'')+'"><label class="paper-check"><input type="checkbox" data-paper-pick="'+b.id+'" '+(paperSelected.has(b.id)?'checked':'')+' '+(b.status==='ready'?'':'disabled')+'><span><strong>'+esc(b.title)+'</strong><small>'+esc(statusText(b))+' · '+(b.total||'—')+' 页</small></span></label></article>';
 }).join(''):'<div class="empty-book"><strong>先上传老师的论文</strong><p>仅支持可提取文字的 PDF；上传后选择论文即可开始。</p></div>';
 $('paperPicker').querySelectorAll('[data-paper-pick]').forEach(el=>el.onchange=()=>{
  if(el.checked&&paperSelected.size>=3){el.checked=false;message('一次最多融合3篇论文。');return;}
  if(el.checked)paperSelected.add(el.dataset.paperPick);else paperSelected.delete(el.dataset.paperPick);
  renderPaperPicker();$('paperPlanResult').hidden=true;
 });
 paperControls();
}
function paperRequest(){return {teacher:$('paperTeacher').value.trim(),kind:$('paperKind').value,papers:[...paperSelected].sort().map(id=>({book_id:id}))};}
async function showPapers(){
 await flushDraft();await flushPaperDrafts();current=null;watchedJob=null;view('paperView');await refresh();
 if(!paperSupported())return;
 const metadata=await api('/api/papers');
 for(const meta of metadata)if(!paperMeta.has(meta.book_id))paperMeta.set(meta.book_id,meta);
 if(!$('paperTeacher').value&&metadata.length)$('paperTeacher').value=metadata[0].teacher;
 renderPaperPicker();const jobs=await paperHistory();
 if(!paperJob&&jobs.length)await openPaperJob(jobs[0].id);
}
async function paperHistory(){
 const jobs=await api('/api/paper-jobs');
 $('paperHistory').innerHTML=jobs.length?jobs.map(j=>'<button class="paper-history-item" data-paper-job="'+j.id+'"><strong>'+esc(j.teacher)+' · '+({ready:'已完成',failed:'需处理',running:'精读中',queued:'排队中'}[j.status]||j.status)+'</strong><span>'+esc(new Date(j.created*1000).toLocaleString('zh-CN'))+'</span></button>').join(''):'<div class="muted paper-history-empty">尚无精读记录。选择论文后，开始第一批训练。</div>';
 $('paperHistory').querySelectorAll('[data-paper-job]').forEach(b=>b.onclick=safe(()=>openPaperJob(b.dataset.paperJob)));
 return jobs;
}
async function planPapers(){
 const plan=await api('/api/papers/plan',paperRequest());
 $('paperPlanResult').hidden=false;
 $('paperPlanResult').innerHTML='<strong>范围核对 · 不产生模型费用</strong>'+plan.papers.map(p=>'<p>'+esc(p.title)+'：'+p.readable_pages+'/'+p.total_pages+' 页有文字，'+p.characters+' 字符'+'</p>'+p.warnings.map(w=>'<p>'+esc(w)+'</p>').join('')).join('')+'<p>共 '+plan.chunks+' 个精读分段，'+plan.cached_chunks+' 个已有成功缓存。另有 '+plan.review_steps+' 次逐篇复核，其中 '+plan.cached_reviews+' 次有成功缓存。新生成或明确继续时预计最多 '+plan.max_new_calls+' 次模型请求，失败即停。完成后生成所选题型。</p><p>这是物理页码及提取文字覆盖情况，不保证扫描页、公式、图表或版面顺序完整。每篇上限12万字符。</p>';
 return plan;
}
async function startPapers(){
 $('paperGenerate').disabled=true;
 try{await planPapers();const job=await api('/api/paper-jobs',paperRequest());await openPaperJob(job.id);await paperHistory();}
 finally{paperControls();}
}
async function openPaperJob(id){
 await flushPaperDrafts();paperWatch=null;paperJob=await api('/api/paper-jobs/'+id);renderPaperJob();
 if(['queued','running'].includes(paperJob.status))watchPaper(id).catch(e=>{if(paperWatch===id)message(e.message);});
}
async function watchPaper(id){
 paperWatch=id;
 while(paperWatch===id){
  const job=await api('/api/paper-jobs/'+id);if(paperWatch!==id)return;paperJob=job;
  renderPaperStatus();
  if(!['queued','running'].includes(job.status)){paperWatch=null;renderPaperJob();await paperHistory();await refresh();if(!$('paperView').hidden&&!$('paperResult').hidden)$('paperResult').scrollIntoView({block:'start'});return;}
  await new Promise(resolve=>setTimeout(resolve,1000));
 }
}
function renderPaperStatus(){
 if(!paperJob)return;
 paperBusy=['queued','running'].includes(paperJob.status);paperControls();$('paperJobStatus').hidden=false;
 $('paperJobStatus').innerHTML=paperJob.status==='failed'?'<strong>本批尚未完成</strong><p>'+esc(paperJob.error)+'</p><p>已完成分段保留；未自动重新付费。下方如有精读观点，可以先核对。</p>'+(paperJob.can_recheck?'<button class="button secondary" id="paperRecheck">免费核验本地结果</button> ':'')+'<button class="button secondary" id="paperRetry">继续未完成步骤 · 可能收费</button>':'<strong>'+esc(paperJob.message||'等待处理')+'</strong><p>本批相关材料的累计请求 '+paperJob.attempts+' 次（含相同材料其他题型的缓存步骤）。关闭网页不会取消后台任务。</p>';
 if(paperJob.can_complete_missing){
  const names=paperJob.missing_types.map(kind=>kind==='essays'?'论述题':'名词解释').join('、');
  $('paperJobStatus').innerHTML+='<p>尚未生成：'+esc(names)+'。已有题目与草稿已保留。</p><button class="button secondary" id="paperCompleteMissing">补生成'+esc(names)+' · 可能收费</button>';
  $('paperCompleteMissing').onclick=safe(async()=>{if(!confirm('将复用已完成的精读，只请求缺失题型，可能新增一次出题请求费用。已有题目及草稿保留。确认继续？'))return;await flushPaperDrafts();$('paperCompleteMissing').disabled=true;try{const job=await api('/api/paper-jobs',{...paperJob.payload,complete_missing:true,retry_paid:true});await openPaperJob(job.id);}finally{if($('paperCompleteMissing'))$('paperCompleteMissing').disabled=false;}});
 }
 if($('paperRecheck'))$('paperRecheck').onclick=safe(()=>retryPapers(true));
 if($('paperRetry'))$('paperRetry').onclick=safe(async()=>{if(confirm('将先复用成功步骤与可核验响应，再向 DeepSeek 请求未完成步骤，可能新增费用。确认继续？'))await retryPapers(false);});
}
async function retryPapers(local){
 const request={...paperJob.payload,local_only:local,retry_paid:!local};
 $('paperJobStatus').querySelectorAll('button').forEach(b=>b.disabled=true);
 try{const job=await api('/api/paper-jobs',request);await openPaperJob(job.id);}finally{paperControls();}
}
function evidenceHTML(point){
 return '<details class="paper-evidence"><summary>核对 '+point.sources.length+' 处原文</summary>'+(point.quotes?.length?'<div class="paper-support-quotes"><strong>支持该概括的原文片段</strong>'+point.quotes.map(quote=>'<blockquote><small>PDF 第 '+quote.page+' 页</small><p>'+esc(quote.text)+'</p></blockquote>').join('')+'</div>':'')+point.sources.map(s=>'<blockquote><strong>'+esc(s.title)+' · PDF 第 '+s.page+' 页</strong><p>'+esc(s.text)+'</p><button class="text-button" data-paper-source="'+s.book_id+'" data-page="'+s.page+'">打开整页文本与 PDF ↗</button></blockquote>').join('')+'</details>';
}
function paperFacetsHTML(point){
 if(!point.facets)return '';
 const details='<dl class="paper-point-facets">'+Object.entries(point.facets).map(([label,value])=>'<div><dt>'+esc(label)+'</dt><dd>'+esc(value)+'</dd></div>').join('')+'</dl>';
 return ['question','method'].includes(point.category)?'<details class="paper-compact-facets"><summary>查看补充信息</summary>'+details+'</details>':details;
}
function paperDirectAnswer(value){
 let result=String(value||''),previous;
 do{previous=result;result=result.replace(/(^|[。！？；，,\n])\s*(?:(?:本文|作者|论文作者|该文作者|该论文作者)(?:认为|指出|主张)|(?:论文|文章|该研究)(?:认为|指出|提出|表明))\s*(?:[，,:：]\s*)?/g,'$1').trim();}while(result!==previous);
 return result;
}
function paperScenarioText(value){
 return String(value||'').replace(/^(\s*)(?:假设(?!检验)|假定|设想|设定)[\s：:，,]*/, '$1').trim();
}
function paperExamQuestion(q){
 if(q.type!=='essays')return q.question;
 // Old saved questions retain their original text; hide supplied solution hints in exam view.
 const hint=/(?:请|试|要求|须|应)(?:你)?(?:结合|运用|依据|根据|参照|借助)|(?:结合|运用|依据|根据|参照|借助)[^。！？\n]{0,180}(?:理论|学派|论文|作者观点)/;
 if(!hint.test(q.question))return q.question;
 const parts=q.question.split(/[，,；;]/);
 const tail=parts.slice(1).reverse().find(part=>/(?:分析|论述|讨论|评析|评价|阐释|解释)/.test(part))||'';
 const remainder=tail.replace(/^.*?(?=(?:分析|论述|讨论|评析|评价|阐释|解释))/, '');
 return remainder&&!hint.test(remainder)&&!/(?:理论|学派|论文|模式|观点)/.test(remainder)&&/(?:分析|论述|讨论|评析|评价|阐释|解释)/.test(remainder)?remainder:'请分析上述情境中的主要矛盾及其形成机制，并讨论可能的影响与应对边界。';
}
function renderPaperJob(){
 renderPaperStatus();if(!paperJob)return;const result=paperJob.result;
 $('paperResult').hidden=!result.points?.length;if(!result.points?.length)return;
 const terms=result.questions.filter(q=>q.type==='terms'),essays=result.questions.filter(q=>q.type==='essays');
 if(paperTab==='terms'&&!terms.length||paperTab==='essays'&&!essays.length)paperTab='analysis';
 $('paperResult').innerHTML='<div class="section-heading"><div><h2>'+esc(result.teacher)+' · 精读与训练</h2><p>'+result.papers.length+' 篇论文 · '+esc(result.model)+' · 结果保存在本机</p></div></div><div class="notice info">出处可核对，解释与理论归属以论文原文为准。</div>'+(result.analysis_stage==='candidates'?'<div class="notice">以下是分段候选，尚未完成逐篇复核。请勿将其当作最终概括。</div>':(!result.analysis_style||result.review_style!=='detail-v1')?'<p class="muted">这是升级前保存的精读记录，未应用新的逐篇复核规则。</p>':'')+(paperJob.status!=='ready'?'<div class="notice">这是已保存的部分精读结果，尚未完成整批出题。</div>':'')+(!result.questions.length&&paperJob.status==='failed'?'<div class="notice info">名词解释和论述题尚未生成：精读或复核步骤未完成。请先使用上方的免费本地核验；仍有步骤未完成时，再明确选择继续生成。</div>':'')+(result.notes.length?'<details class="notice"><summary>材料与生成说明</summary>'+result.notes.map(n=>'<p>'+esc(n)+'</p>').join('')+'</details>':'')+(result.issues.length?'<details class="notice"><summary>查看跳过内容与数量说明（'+result.issues.length+'项）</summary>'+result.issues.map(i=>'<p>'+esc(i)+'</p>').join('')+'</details>':'')+'<div class="paper-tabs" role="tablist" aria-label="精读结果与训练题"><button role="tab" data-paper-tab="analysis" aria-selected="'+(paperTab==='analysis')+'">核心观点与理论 <b>'+result.points.length+'</b></button><button role="tab" data-paper-tab="terms" aria-selected="'+(paperTab==='terms')+'" '+(terms.length?'':'disabled')+'>名词解释 <b>'+terms.length+'</b></button><button role="tab" data-paper-tab="essays" aria-selected="'+(paperTab==='essays')+'" '+(essays.length?'':'disabled')+'>论述题 <b>'+essays.length+'</b></button></div><div id="paperTabContent"></div>';
 if(paperTab==='analysis'){
  const order=['theory','question','argument','method','finding','limitation'];
  $('paperTabContent').innerHTML=result.papers.map((paper,paperIndex)=>{
   const points=result.points.filter(point=>point.paper_id===paper.book_id);
   const groups=order.map(category=>({category,title:paperCategories[category],points:points.filter(point=>point.category===category)}));
   const other=points.filter(point=>!order.includes(point.category));
   if(other.length)groups.push({category:'other',title:'其他内容',points:other});
   const visible=groups.filter(group=>group.points.length);
   const prefix='paper-'+paperIndex+'-category-';
   return '<section class="paper-document" data-paper-document="'+paper.book_id+'"><header class="paper-document-heading"><h3>'+esc(paper.title)+'</h3><p class="muted">可提取文字 '+paper.readable_pages+'/'+paper.total_pages+' 页 · '+points.length+' 条精读内容</p></header>'+(result.paper_reviews?.[paper.book_id]?'<p class="paper-review-note">精读说明：'+esc(result.paper_reviews[paper.book_id])+'</p>':'')+(points.length?'<nav class="paper-category-nav" aria-label="'+esc(paper.title)+'的内容分类">'+visible.map(group=>'<a href="#'+prefix+group.category+'">'+esc(group.title)+' <span>'+group.points.length+'</span></a>').join('')+'</nav>':'<p class="muted">本篇暂无已保存的精读内容。</p>')+visible.map(group=>'<section class="paper-analysis-group" id="'+prefix+group.category+'" data-paper-category="'+group.category+'"><div class="paper-category-heading"><h4>'+esc(group.title)+'</h4><span>'+group.points.length+' 条</span></div>'+group.points.map(point=>{
    const pages=[...new Set(point.sources.map(source=>source.page))].sort((a,b)=>a-b);
    return '<article class="panel paper-point" id="point-'+point.id+'"><div class="paper-point-label"><span>'+esc(paperCategories[point.category]||'其他内容')+'</span><span class="attribution '+point.attribution+'">'+esc(paperAttr[point.attribution])+'</span></div><h3>'+esc(point.title)+'</h3><p class="paper-point-origin">来源：'+esc(paper.title)+' · PDF 第 '+pages.join('、')+' 页</p><p class="paper-text">'+esc(point.text)+'</p>'+paperFacetsHTML(point)+(point.basis_status==='uncertain'?'<p class="notice">待核对：'+esc(point.uncertainty)+'</p>':'')+'<p class="muted">归属：'+esc(point.owner)+'</p>'+evidenceHTML(point)+'</article>';
   }).join('')+'</section>').join('')+'</section>';
  }).join('');
 }else{
  const questions=paperTab==='terms'?terms:essays;
  $('paperTabContent').innerHTML=(paperTab==='essays'?'<p class="muted">情境论述：围绕具体矛盾组织论证，将论文观点用于分析。论文依据可逐条核对。</p>':'')+'<p class="muted">先作答，再展开参考答案。草稿自动保存，不自动评分、不改变书籍掌握记录。</p>'+questions.map((q,index)=>{
   const state=paperJob.answers[q.id]||{};
   return '<article class="panel paper-question"><div class="card-tag">'+(q.type==='terms'?'名词解释':'论述题')+' · '+(index+1)+' / '+questions.length+'</div>'+(q.scenario?'<section class="paper-scenario"><p>'+esc(paperScenarioText(q.scenario))+'</p></section>':q.type==='essays'?'<p class="muted">历史题目 · 更新前生成，保留原题与草稿。</p>':'')+'<h3>'+esc(paperExamQuestion(q))+'</h3><label class="label" for="paper-draft-'+q.id+'">你的作答（可选）</label><textarea id="paper-draft-'+q.id+'" data-paper-draft="'+q.id+'" '+(paperBusy?'disabled ':'')+'maxlength="12000" placeholder="先尝试组织自己的解释与论证…">'+esc(state.draft||'')+'</textarea><div class="muted" id="paper-save-'+q.id+'" role="status">草稿仅保存在本机。</div>'+(state.revealed?'<section class="paper-answer"><div class="card-tag">参考答案 · 约 '+q.length+' 字 · AI生成，需核对</div>'+q.warnings.map(w=>'<p class="notice">'+esc(w)+'</p>').join('')+q.sections.map((s,sectionIndex)=>'<div class="paper-answer-section"><div class="paper-point-label"><h4>'+(q.type==='essays'?'<span class="paper-answer-number">'+(sectionIndex+1)+'</span>':'')+esc(q.type==='essays'?(paperDirectAnswer(s.heading)||'论证'):s.heading)+'</h4><span class="attribution '+s.attribution+'">'+esc(q.type==='essays'?({author_claim:'材料依据',cited_theory:'理论依据',ai_synthesis:'AI 综合分析'}[s.attribution]):paperAttr[s.attribution])+'</span></div><p class="paper-text">'+esc(q.type==='essays'?paperDirectAnswer(s.text):s.text)+'</p><div class="paper-point-links">观点依据：'+s.point_ids.map(id=>{const point=result.points.find(p=>p.id===id);return '<button class="text-button" data-point-link="'+id+'">'+esc(q.type==='essays'?paperDirectAnswer(point.title):point.title)+' · '+esc(point.sources[0].title)+' 第'+point.sources[0].page+'页 ↗</button>';}).join('')+'</div></div>').join('')+'</section>':'<button class="button primary paper-reveal" data-paper-reveal="'+q.id+'" '+(paperBusy?'disabled':'')+'>我已尝试，查看参考答案</button>')+'</article>';
  }).join('');
 }
 $('paperResult').querySelectorAll('[data-paper-tab]').forEach(b=>b.onclick=safe(async()=>{await flushPaperDrafts();paperTab=b.dataset.paperTab;renderPaperJob();}));
 $('paperResult').querySelectorAll('[data-paper-source]').forEach(b=>b.onclick=safe(()=>source(b.dataset.paperSource,Number(b.dataset.page))));
 $('paperResult').querySelectorAll('[data-point-link]').forEach(b=>b.onclick=safe(async()=>{await flushPaperDrafts();paperTab='analysis';renderPaperJob();document.getElementById('point-'+b.dataset.pointLink)?.scrollIntoView({block:'center'});}));
 $('paperResult').querySelectorAll('[data-paper-reveal]').forEach(b=>b.onclick=safe(async()=>{b.disabled=true;try{await flushPaperDrafts();await api('/api/paper-jobs/'+paperJob.id+'/answers/'+b.dataset.paperReveal,{action:'reveal'});paperJob=await api('/api/paper-jobs/'+paperJob.id);renderPaperJob();}catch(e){b.disabled=false;throw e;}}));
 $('paperResult').querySelectorAll('[data-paper-draft]').forEach(input=>input.oninput=()=>{
  const qid=input.dataset.paperDraft;clearTimeout(paperTimers.get(qid));$('paper-save-'+qid).textContent='有未保存的修改…';paperTimers.set(qid,setTimeout(()=>savePaperDraft(input).catch(e=>message(e.message)),400));
 });
}
function savePaperDraft(input){
 if(!paperJob||!input.isConnected)return paperPending;
 const jid=paperJob.id,qid=input.dataset.paperDraft,draft=input.value;
 if((paperJob.answers[qid]?.draft||'')===draft)return paperPending;
 paperPending=paperPending.catch(()=>{}).then(()=>api('/api/paper-jobs/'+jid+'/answers/'+qid,{action:'draft',draft})).then(()=>{
  if(paperJob?.id===jid){paperJob.answers[qid]={...paperJob.answers[qid],draft};if($('paper-save-'+qid))$('paper-save-'+qid).textContent='草稿已保存到本机';}
 });return paperPending;
}
async function flushPaperDrafts(){for(const id of paperTimers.values())clearTimeout(id);paperTimers.clear();for(const input of document.querySelectorAll('[data-paper-draft]'))await savePaperDraft(input);await paperPending;}
async function uploadPaper(file){
 if(!file)return;if(!file.name.toLowerCase().endsWith('.pdf')||file.size>30*1024*1024)throw Error('请选择不超过30MB的文字版PDF。');
 await showPapers();const status=$('paperUploadStatus');status.hidden=false;status.textContent='正在上传论文…';$('paperFile').disabled=true;
 try{
  const book=await new Promise((resolve,reject)=>{
   const xhr=new XMLHttpRequest();xhr.open('POST','/api/books');xhr.setRequestHeader('X-Study-Token',config.token);xhr.setRequestHeader('X-Filename',encodeURIComponent(file.name));xhr.setRequestHeader('Content-Type','application/pdf');xhr.timeout=60000;
   xhr.upload.onprogress=e=>{if(e.lengthComputable)status.textContent='论文上传 '+Math.round(e.loaded/e.total*100)+'%';};
   xhr.onload=()=>{try{const data=JSON.parse(xhr.responseText);xhr.status<300?resolve(data):reject(Error(data.error));}catch{reject(Error('上传响应异常。'));}};
   xhr.onerror=()=>reject(Error('上传失败，请检查本机服务。'));xhr.ontimeout=()=>reject(Error('上传超时，请重试。'));xhr.send(file);
  });
  await refresh();if(paperSelected.size<3)paperSelected.add(book.id);renderPaperPicker();status.textContent='上传完成，正在解析。可在下方列表查看状态；解析完成后即可选择论文并生成。';
 }catch(e){status.textContent='上传未完成：'+e.message;throw e;}finally{$('paperFile').disabled=false;$('paperFile').value='';}
}
$('papersNav').onclick=safe(showPapers);$('paperUploadButton').onclick=()=>$('paperFile').click();$('paperFile').onchange=safe(()=>uploadPaper($('paperFile').files[0]));
$('paperPlan').onclick=safe(planPapers);$('paperGenerate').onclick=safe(startPapers);
$('paperTeacher').oninput=()=>{$('paperPlanResult').hidden=true;};$('paperKind').onchange=()=>$('paperPlanResult').hidden=true;
window.addEventListener('beforeunload',()=>{
 if(!paperJob||!config)return;
 for(const input of document.querySelectorAll('[data-paper-draft]'))fetch('/api/paper-jobs/'+paperJob.id+'/answers/'+input.dataset.paperDraft,{method:'POST',headers:{'Content-Type':'application/json','X-Study-Token':config.token},body:JSON.stringify({action:'draft',draft:input.value}),keepalive:true}).catch(()=>{});
});
