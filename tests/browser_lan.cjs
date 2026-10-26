const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const http=require('node:http');
const path=require('node:path');
const fs=require('node:fs/promises');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const py=process.env.STUDY_PYTHON||path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe');
let child,browser,proxy;
(async()=>{
 child=spawn(py,['-B',path.join(root,'app.py'),'--demo','--port','0','--lan-ip','192.168.50.2','--data-dir',path.join(root,'work','lan-browser-'+Date.now())],{cwd:root,windowsHide:true,stdio:['ignore','pipe','ignore']});
 let output='';child.stdout.setEncoding('utf8');child.stdout.on('data',s=>output+=s);
 for(let i=0;i<100&&!output.includes('手机访问码：');i++)await new Promise(r=>setTimeout(r,100));
 const backendPort=Number(output.match(/127\.0\.0\.1:(\d+)/)?.[1]);assert.ok(backendPort);
 const code=output.match(/手机访问码：([^\r\n]+)/)?.[1];assert.ok(code,'temporary code printed');
 // Proxy simulates the LAN Host on loopback; backend sees no local bypass.
 proxy=http.createServer((req,res)=>{const headers={...req.headers,host:'192.168.50.2:'+backendPort};if(headers.origin==='http://'+req.headers.host)headers.origin='http://192.168.50.2:'+backendPort;const forward=http.request({host:'127.0.0.1',port:backendPort,path:req.url,method:req.method,headers},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res);});forward.on('error',()=>res.end());req.pipe(forward);});
 await new Promise((resolve,reject)=>{proxy.once('error',reject);proxy.listen(0,'127.0.0.1',resolve);});
 const base='http://127.0.0.1:'+proxy.address().port;
 const executable=await fs.access(chromium.executablePath()).then(()=>chromium.executablePath()).catch(()=> 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe');
 browser=await chromium.launch({headless:true,executablePath:executable,env:{...process.env,TEMP:path.join(root,'work'),TMP:path.join(root,'work')}});
 const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);await page.locator('#lanCode').waitFor();
 await page.fill('#lanCode','wrong');await page.click('#lanConnect');await page.waitForFunction(()=>document.querySelector('#lanError').textContent.length>0);
 await page.fill('#lanCode',code);await page.click('#lanConnect');await page.locator('#emptyUpload').waitFor();
 for(const width of [360,390,768]){await page.setViewportSize({width,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no horizontal overflow '+width);}
 for(const width of [320,360,390,430]){
   await page.setViewportSize({width,height:844});
   const boxes=await page.locator('.sidebar nav button').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {y:r.y,w:r.width,h:r.height,overflow:n.scrollWidth>n.clientWidth};}));
   assert.equal(new Set(boxes.map(b=>b.y)).size,1,'navigation stays in one row '+width);
   assert.ok(boxes.every(b=>b.h>=44&&!b.overflow),'readable navigation '+width);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no overflow '+width);
 }
 for(const width of [320,390,430]){
   await page.setViewportSize({width,height:844});
   const heading=await page.locator('.focus-card h2').first().evaluate(el=>({height:el.getBoundingClientRect().height,line:parseFloat(getComputedStyle(el).lineHeight)}));
   assert.ok(heading.height<heading.line*2.2,'hero has exactly two complete lines '+width);
 }
 await page.click('#reviewNav');await page.locator('#reviewList .empty-range').waitFor();
 await page.locator('#reviewList').evaluate(el=>{el.innerHTML='<section><div class="section-heading"><div><h3>合成测试：传播学经典理论一句话背诵长标题</h3><p>6 条待复习</p></div><button class="button primary" data-review="fixture">开始复习 →</button></div></section>';});
 for(const width of [320,390,430]){
   await page.setViewportSize({width,height:844});
   const button=await page.locator('[data-review="fixture"]').evaluate(el=>({height:el.getBoundingClientRect().height,line:parseFloat(getComputedStyle(el).lineHeight),padding:parseFloat(getComputedStyle(el).paddingTop)+parseFloat(getComputedStyle(el).paddingBottom)}));
   assert.ok(button.height<button.line*1.5+button.padding+4,'review button stays one line '+width);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'review no overflow '+width);
 }
 await page.click('#homeNav');
 const config=await(await page.request.get(base+'/api/config')).json();
 await page.route('**/api/config',route=>route.fulfill({json:{...config,mode:'deepseek',ready:true,local_recheck:true,model:'deepseek-flash'}}));
 await page.reload();await page.locator('.ai-banner .banner-toggle').waitFor();
 assert.equal(await page.locator('#aiUsageDetails').isVisible(),false);
 await page.click('.banner-toggle');assert.equal(await page.locator('#aiUsageDetails').isVisible(),true);
 await page.click('.banner-toggle');assert.equal(await page.locator('#aiUsageDetails').isVisible(),false);
 assert.equal((await page.request.get(base+'/api/books')).status(),200);
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:path.join(root,'work','mobile-lan-overview.png'),fullPage:true});
 await page.click('#libraryNav');
 const pdf=await fs.readFile(path.join(root,'work','演示参考书_合成测试材料.pdf'));
 await page.locator('#pdfFile').setInputFiles({name:'手机端长标题布局合成测试资料.pdf',mimeType:'application/pdf',buffer:pdf});
 await page.locator('#startStudy').waitFor();await page.click('#startStudy');await page.locator('#backHome').waitFor();
 for(const width of [320,390,430]){
   await page.setViewportSize({width,height:844});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'study no overflow '+width);
   const back=await page.locator('#backHome').boundingBox(),size=await page.locator('#fontSize').boundingBox();
   assert.ok(Math.abs(back.y-size.y)<12,'study toolbar shares row '+width);
 }
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(root,'work','mobile-study-layout.png'),fullPage:true});
 await page.click('#privacyBtn');
 for(const width of [320,390,430]){
  await page.setViewportSize({width,height:844});
  const dialog=await page.locator('#privacyDialog').boundingBox();assert.ok(dialog.x>=0&&dialog.x+dialog.width<=width+1,'privacy dialog fits '+width);
  const close=await page.locator('#closePrivacy').boundingBox();assert.ok(close.width>=45&&close.height<45,'privacy close fits '+width);
 }
 await page.click('#closePrivacy');
 assert.deepEqual(errors,[]);console.log('PASS: phone login, invalid code, session persistence, 360/390/768px layout');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(proxy)await new Promise(r=>proxy.close(r));if(child)child.kill();});
