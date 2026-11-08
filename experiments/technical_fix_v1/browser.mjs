// Independent Chrome DevTools inspection. Local files, external requests blocked.
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';import os from 'node:os';import {pathToFileURL,fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const out=path.join(root,'artifacts/technical-fix-v1');
const profile=await mkdtemp(path.join(os.tmpdir(),'tech-fix-browser-'));
const chrome=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-component-update','--disable-sync','--metrics-recording-only','--disable-features=MediaRouter,OptimizationHints','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{stdio:'ignore'});
const pause=ms=>new Promise(r=>setTimeout(r,ms));let ws;let seq=0;const pending=new Map();const errors=[],remote=[],results=[];
try{
 let port;for(let i=0;i<150;i++){try{port=(await readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0];break}catch{if(chrome.exitCode!==null)throw Error('Chrome exited');await pause(100)}}
 if(!port)throw Error('Chrome startup timeout');
 const pages=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json();ws=new WebSocket(pages.find(x=>x.type==='page').webSocketDebuggerUrl);await new Promise((ok,no)=>{ws.onopen=ok;ws.onerror=no});
 ws.onmessage=e=>{const r=JSON.parse(e.data);if(r.method==='Runtime.exceptionThrown')errors.push(r.params);if(r.method==='Network.requestWillBeSent'&&/^https?:/.test(r.params.request.url))remote.push(r.params.request.url);if(r.id){const p=pending.get(r.id);if(p){pending.delete(r.id);r.error?p.no(Error(JSON.stringify(r.error))):p.ok(r.result)}}};
 const call=(method,params={})=>new Promise((ok,no)=>{const id=++seq;pending.set(id,{ok,no});setTimeout(()=>{if(pending.has(id)){pending.delete(id);no(Error('CDP timeout '+method))}},30000);ws.send(JSON.stringify({id,method,params}))});
 const js=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result?.value};
 const nav=async(rel,ready)=>{const url=pathToFileURL(path.join(root,rel)).href;await call('Page.navigate',{url});for(let i=0;i<150;i++){if(await js(`location.href===${JSON.stringify(url)}&&document.readyState==='complete'&&(${ready})`))return;await pause(100)}throw Error('Page timeout '+rel)};
 const screenshot=async(name)=>{console.log('Screenshot '+name);await js('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');let r=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await writeFile(path.join(out,'screenshots',name),Buffer.from(r.data,'base64'))};
 await call('Runtime.enable');await call('Page.enable');await call('Network.enable');await call('Network.setBlockedURLs',{urls:['http://*','https://*']});await call('Emulation.setDeviceMetricsOverride',{width:1520,height:2400,deviceScaleFactor:1,mobile:false});
 const charts=[['stock-er03','artifacts/earnings-reaction-v1/er03/chart-upgrade/TIPSMUSIC.html','2026-08-18'],['stock-latest','artifacts/technical-integration-v2/charts/stocks/FY2027-Q1-PGEL-S3.html','2026-08-11'],['tyres','artifacts/technical-integration-v2/charts/indices/FY2027-Q1-TYRES-index.html','2026-10-30'],['ems','artifacts/technical-integration-v2/charts/indices/FY2027-Q1-EMS-index.html','2026-06-19'],['transformers','artifacts/technical-integration-v2/charts/indices/FY2027-Q1-TRANSFORMERS-index.html','2026-03-10']];
 for(const [name,rel,day] of charts){
  await nav(rel,'Boolean(window.ER03C?.state.ready)');await js(`ER03C.select(ER03C.data.rows.findIndex(r=>r.date===${JSON.stringify(day)}))`);
  const r=await js(`(()=>{const E=ER03C,i=E.state.selected,r=E.data.rows[i],fields=[];for(const [k,s]of Object.entries(E.series)){let d=s.dataByIndex(i);fields.push({field:k,plotted:d??null})}return {chart:location.pathname,date:r.date,selected:r,plotted:fields,readouts:Object.fromEntries(['inspection','dtr-box','dss-value','volume-value','rs-value','activity-value','provenance'].map(id=>[id,document.getElementById(id)?.textContent])),contextText:document.getElementById('market-context')?.textContent,crosshairs:E.state.crosshairs,toggles:[...document.querySelectorAll('input[type=checkbox]')].map(x=>({id:x.id,checked:x.checked})),panels:Object.keys(E.charts)}})()`);
  if(name!=='stock-er03'){
   if(!r.contextText.includes('237 daily / 13216 sampled minute observations'))throw Error('GIFT inventory missing '+name);
   const stale=await js(`[...document.querySelectorAll('#market-context tr')].some(r=>r.firstElementChild?.textContent.startsWith('GIFT_NIFTY / '))`);
   if(stale)throw Error('Superseded GIFT context '+name);
  }
  // Real pointer crosshair into price plot, then save the DOM and selected date again.
  const xy=await js(`(()=>{const E=ER03C,r=E.data.rows[E.state.selected],c=E.charts.price,e=document.getElementById('price').getBoundingClientRect();return {x:e.x+c.timeScale().timeToCoordinate(r.date),y:e.y+100}})()`);
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:Math.max(100,Math.min(1400,xy.x??400)),y:Math.max(100,Math.min(1100,xy.y))});
  await js('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');r.afterPointer=await js('({selected:ER03C.data.rows[ER03C.state.selected].date,actual:ER03C.state.actualCrosshairs})');
  await js(`ER03C.select(ER03C.data.rows.findIndex(r=>r.date===${JSON.stringify(day)}))`);
  r.toggleResults=await js(`(()=>{let a=[];for(let x of document.querySelectorAll('input[type=checkbox]')){const b=x.checked;x.click();a.push({id:x.id,changed:x.checked!==b});x.click()}return a})()`);
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:5,y:5});
  await js(`(()=>{const E=ER03C,i=E.data.rows.findIndex(r=>r.date===${JSON.stringify(day)});E.range(E.data.rows[Math.max(0,i-35)].date,E.data.rows[Math.min(E.data.rows.length-1,i+35)].date)})()`);
  await pause(300);
  const physical=await js(`(()=>{const E=ER03C,r=E.data.rows.find(r=>r.date===${JSON.stringify(day)}),el=document.getElementById('price').getBoundingClientRect();return{x:el.x+E.charts.price.timeScale().timeToCoordinate(r.date),y:el.y+150}})()`);
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:physical.x,y:physical.y});await pause(300);
  r.screenshotDate=await js('ER03C.data.rows[ER03C.state.selected].date');
  await screenshot('2026-11-05-'+name+'-'+day+'.png');
  r.screenshotReadouts=await js(`({selected:ER03C.data.rows[ER03C.state.selected].date,text:document.getElementById('inspection').textContent})`);
  if(r.screenshotReadouts.selected!==day)throw Error('Screenshot date changed '+name+' '+r.screenshotReadouts.selected);
  results.push({kind:name,...r});
 }
 await writeFile(path.join(out,'evidence/BROWSER_PARTIAL.json'),JSON.stringify({results,errors,remote}));
 await nav('artifacts/technical-integration-v2/charts/sector-rotation.html','Boolean(window.ROTATION) || document.querySelectorAll("canvas").length>=4');
 results.push({kind:'sector-rotation',data:await js(`({globals:Object.keys(window).filter(k=>/rotat/i.test(k)),controls:[...document.querySelectorAll('select')].map(x=>({id:x.id,value:x.value,options:[...x.options].map(o=>({value:o.value,text:o.text}))})),text:document.body.innerText,context:TECH06.data.context[TECH06.selectedDate],dataKeys:Object.keys(TECH06.data),selectedRows:TECH06.data.rows.filter(r=>r.date===TECH06.selectedDate),series:TECH06.charts.price.panes()[0].getSeries().map(s=>({title:s.options().title,data:s.data()})),rs:TECH06.charts.rs.panes()[0].getSeries().map(s=>s.data()),selected:TECH06.selectedDate})`)});
 const giftRows=await js(`TECH06.data.context[TECH06.selectedDate].filter(r=>r.series.includes('GIFT'))`);
 if(!giftRows.length||giftRows.some(r=>r.missing_data_reason.includes('NO_VERIFIED_QUOTATION')))throw Error('Stale sector GIFT');
 await screenshot('2026-11-05-sector-rotation.png');
 await nav('artifacts/technical-integration-v2/charts/global-market.html','Boolean(window.GLOBAL_REVIEW)');
 for(const key of ['DJIA','GIFT_NIFTY','USDINR','BRENT','WTI','US_TECH100']){
  await js(`document.getElementById('series').value=${JSON.stringify(key)};GLOBAL_REVIEW.windows();GLOBAL_REVIEW.inspect('2026-03-10','PREOPEN')`);
  const r=await js(`(()=>{const G=GLOBAL_REVIEW;const pane=G.charts.quote.panes()[0];const ss=pane.getSeries();return {kind:'global-'+${JSON.stringify(key)},bars:G.activeBars,series:ss.map(s=>s.data()),readout:document.getElementById('coverage').textContent,checkpoints:document.getElementById('checkpoint').innerText}})()`);
  // Move the pointer to a known loaded candlestick and retain actual OHLC crosshair text.
  const xy=await js(`(()=>{const a=GLOBAL_REVIEW.activeBars,i=Math.max(0,a.length-8),r=a[i],e=document.getElementById('quote-chart').getBoundingClientRect();return{x:e.x+GLOBAL_REVIEW.charts.quote.timeScale().timeToCoordinate(r.date),y:e.y+100}})()`);
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:xy.x,y:xy.y});r.crosshair=await js('document.getElementById("quote-readout").textContent');
  await screenshot('2026-11-05-global-'+key+'.png');results.push(r);
  if(key!=='DJIA'){
   await js('document.getElementById("window").selectedIndex=1;GLOBAL_REVIEW.plot()');
   results.push(await js(`({kind:'minute-'+${JSON.stringify(key)},bars:GLOBAL_REVIEW.activeBars,series:GLOBAL_REVIEW.charts.quote.panes()[0].getSeries().map(s=>s.data()),axis:document.getElementById('quote-axis').textContent})`));
   if(key==='GIFT_NIFTY')await screenshot('2026-11-05-global-GIFT-minute.png');
  }
 }
 await writeFile(path.join(out,'evidence/BROWSER_RESULTS.json'),JSON.stringify({run_utc:new Date().toISOString(),networkBlocked:true,errors,remote,results},null,2)+'\n');console.log(JSON.stringify({charts:results.length,javascriptErrors:errors.length,externalRequests:remote.length,screenshots:'artifacts/technical-fix-v1/screenshots'}));
}catch(e){await writeFile(path.join(out,'evidence/BROWSER_FAILURE.json'),JSON.stringify({error:String(e),results,errors,remote},null,2));throw e}finally{ws?.close();chrome.kill('SIGTERM')}
