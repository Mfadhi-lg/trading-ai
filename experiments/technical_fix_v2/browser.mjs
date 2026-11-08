// Independent Chrome DevTools inspection. Local files, external requests blocked.
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';import os from 'node:os';import {pathToFileURL,fileURLToPath} from 'node:url';
const root=process.env.TECH_FIX_REVIEW_ROOT||path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const out=path.join(root,'artifacts/technical-fix-v2');
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
 const assert=async(name,expr)=>{if(!await js(expr))throw Error(name+'; JS errors '+JSON.stringify(errors));results.push({name,passed:true})};
 await nav('artifacts/technical-integration-v2/charts/technical-system.html','Boolean(window.TECH_SYSTEM?.ready)');
 await assert('135 acquired NSE histories and all 85 catalog-mapped relevant indices',`TECH_SYSTEM.data.nse.inventory.filter(r=>r.daily_history_verified).length===135&&TECH_SYSTEM.data.nse.inventory.filter(r=>['BROAD_MARKET','SECTORAL','THEMATIC'].includes(r.classification)&&r.daily_history_verified).length===85`);
 for(const data of await js('TECH_SYSTEM.data.archive.map(r=>({company_id:r.company_id,timeframe:r.timeframe}))')){
  await js(`TECH_SYSTEM.selectArchive(${data.company_id},${JSON.stringify(data.timeframe)},'2026-11-07T00:00:00+05:30')`);
  await assert('Rendered OHLC equals Passport access values '+data.company_id+' '+data.timeframe,`(()=>{let T=TECH_SYSTEM,a=T.active,b=T.series.candles.data();return a.length>0&&a.length===b.length&&a.every((r,i)=>['open','high','low','close'].every(k=>r[k]===b[i][k]))})()`);
  await assert('EMA series and source values agree '+data.company_id+' '+data.timeframe,`(()=>{let T=TECH_SYSTEM;return ['ema10','ema20'].every((k,j)=>{let rows=T.active.filter(r=>r.indicators[k]!=null),plot=T.series[j?'e20':'e10'].data().filter(r=>r.value!==undefined);return rows.length===plot.length&&rows.every((r,i)=>r.indicators[k]===plot[i].value)})})()`);
  if(data.timeframe==='4-hour')await assert('Retained 4h benchmark warmup produces RS55 '+data.company_id,'TECH_SYSTEM.active.some(r=>r.indicators.rs?.rs!=null)');
 }
 for(const at of ['13:15','14:00','15:00','15:29','15:30']){
  await js(`TECH_SYSTEM.selectArchive(555,'4-hour','2026-10-30T${at}:00+05:30')`);
  await assert('No incomplete 4h or future context at '+at,`(()=>{let T=TECH_SYSTEM,cut=Date.parse(T.selected.cutoff);return T.active.every(r=>Date.parse(r.end)<=cut)&&T.globalActive.every(r=>Date.parse(r.end_ist)<cut)&&T.treasuryActive.every(r=>Date.parse(r.observation_convention_ist)<cut)&&T.rotationSeries.every(s=>s.data().every(r=>Date.parse(r.time+'T15:30:00+05:30')<=cut))})()`);
  if(at<'15:30')await assert('Second 4h stays developing at '+at,`!TECH_SYSTEM.active.some(r=>r.start.startsWith('2026-10-30T13:15'))`);
  else await assert('Second 4h completes at 15:30',`TECH_SYSTEM.active.some(r=>r.start.startsWith('2026-10-30T13:15')&&r.end.startsWith('2026-10-30T15:30'))`);
  if(['14:00','15:00','15:29'].includes(at))await assert('Visible developing label at '+at,`document.getElementById('developing').textContent.includes('DEVELOPING 13:15–15:30')`);
 }
 await screenshot('archive-4h-checkpoint.png');
 await js(`TECH_SYSTEM.selectArchive(90,'Daily','2026-11-07T00:00:00+05:30')`);
 await assert('Daily preserved RS and Simple Volume values are displayed',`TECH_SYSTEM.active.some(r=>r.indicators.rs?.rs!=null)&&TECH_SYSTEM.active.some(r=>r.indicators.volume?.volume_sma!=null)&&document.getElementById('features').textContent.includes('RS55')`);
 await screenshot('archive-daily-indicators.png');
 for(const key of ['NSE_INDEX|Nifty Bank','NSE_INDEX|Nifty IT','NSE_INDEX|NIFTY INDIA MFG']){
  await js(`document.getElementById('index').value=${JSON.stringify(key)};document.getElementById('index').dispatchEvent(new Event('change'))`);
  await assert('New historical sector/thematic lines work '+key,`TECH_SYSTEM.rotationSeries[0].data().filter(r=>r.value!==undefined).length>100`);
 }
 await screenshot('official-nse-rotation.png');
 await assert('Sector rankings cover all 23 mapped sectors',`TECH_SYSTEM.data.nse.rotation.filter(r=>r.date==='2026-11-06'&&r.classification==='SECTORAL'&&r.close!=null).length===23`);
 await assert('Official index ATR/DTR uses validated OHLC arithmetic',`TECH_SYSTEM.data.nse.rotation.some(r=>r.instrument_key==='NSE_INDEX|Nifty Bank'&&r.atr14>0&&r.dtr_atr_pct!=null)`);
 await assert('Global bucket ending exactly at checkpoint is excluded',`TECH_SYSTEM.globalActive.every(r=>Date.parse(r.end_ist)<Date.parse('2026-11-07T00:00:00+05:30'))`);
 await js(`TECH_SYSTEM.selectArchive(90,'Daily','2026-11-07T09:15:00+05:30')`);
 for(const key of ['DJIA','SP500','GIFT_NIFTY','USDINR','BRENT','WTI','US_TECH100']){
  await js(`document.getElementById('global-series').value=${JSON.stringify(key)};document.getElementById('global-series').dispatchEvent(new Event('change'))`);
  await assert('Current global candles '+key,`TECH_SYSTEM.globalActive.length>200&&TECH_SYSTEM.series.gseries.data().length===TECH_SYSTEM.globalActive.length&&TECH_SYSTEM.globalActive.at(-1).date==='2026-11-06'`);
 }
 await assert('US Tech 100 visibly labeled as proxy',`document.getElementById('global-series').textContent.includes('provider proxy')`);
 await assert('Invalid original global candles remain absent',`!TECH_SYSTEM.data.globaldata.bars.some(r=>r.unit==='days'&&((r.series==='WTI'&&r.date==='2026-06-18')||(r.series==='GIFT_NIFTY'&&r.date==='2026-07-19')))`);
 await assert('242 Treasury dates and original bp spread values plotted',`TECH_SYSTEM.data.globaldata.treasury.length===242&&TECH_SYSTEM.series.spread.data().length===242&&TECH_SYSTEM.series.spread.data().every((r,i)=>Math.abs(r.value-TECH_SYSTEM.treasuryActive[i].spread_10y_2y_bp)<1e-9)`);
 await screenshot('global-context-and-treasury.png');
 await js(`TECH_SYSTEM.selectArchive(90,'Daily','2026-11-20T18:00:00+05:30')`);
 await assert('Stale global source warning is visible',`document.getElementById('global-warning').textContent.includes('STALE OVER 48 HOURS')`);
 await js(`TECH_SYSTEM.selectArchive(90,'Daily','2025-01-01T09:15:00+05:30')`);
 await assert('Missing source warning is visible',`document.getElementById('global-warning').textContent.includes('MISSING: no completed source bucket')&&TECH_SYSTEM.series.candles.data().length===0`);
 const originals=JSON.parse(await readFile(path.join(root,'artifacts/technical-integration-v2/chart-preservation.json'),'utf8'));
 for(const item of originals){await nav('artifacts/technical-integration-v2/charts/'+item.chart,'Boolean(window.ER03C?.state.ready)');await assert('Original chart still renders '+item.chart,'ER03C.data.rows.length>100&&ER03C.state.ready===true')}
 if(errors.length||remote.length)throw Error('Browser errors or remote requests: '+JSON.stringify({errors,remote}));
 await writeFile(path.join(out,'BROWSER_VALIDATION.json'),JSON.stringify({passed:true,assertions:results.length,checks:results,javascript_errors:errors,remote_requests:remote,local_network_blocked:true},null,2)+'\n');
 console.log(JSON.stringify({passed:true,assertions:results.length,originalCharts:originals.length}));
}catch(e){await writeFile(path.join(out,'BROWSER_FAILURE.json'),JSON.stringify({error:String(e),results,errors,remote},null,2));throw e}finally{ws?.close();chrome.kill('SIGTERM')}
