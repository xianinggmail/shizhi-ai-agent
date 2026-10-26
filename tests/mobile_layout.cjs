// Check compact controls, while allowing prose and document titles to wrap naturally.
module.exports=async function auditMobile(page,label){
 if(page.viewportSize().width>700)return;
 const problems=await page.evaluate(()=>[...document.querySelectorAll('button,.status-pill,.mode-pill')].filter(el=>el.getClientRects().length&&!el.closest('[hidden]')&&!el.matches('.nav,.tool-card,.book-select,.paper-history-item,[data-open-book],[data-overview-paper]')).flatMap(el=>{
  const text=el.textContent.trim();if(!text||text.length>26)return [];
  const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);let node;const tops=[];
  while(node=walker.nextNode()){if(!node.textContent.trim())continue;const range=document.createRange();range.selectNodeContents(node);for(const r of range.getClientRects())if(r.width>0&&!tops.some(y=>Math.abs(y-r.top)<5))tops.push(r.top);}
  return tops.length>1||el.scrollWidth>el.clientWidth+1?[{text,id:el.id,lines:tops.length,overflow:el.scrollWidth-el.clientWidth}]:[];
 }));
 if(problems.length)throw new Error(label+' '+JSON.stringify(problems));
};
