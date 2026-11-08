/* ER-03C presentation only. All financial calculations arrive from Python. */
'use strict';
(() => {
  const D=JSON.parse(document.getElementById('chart-data').textContent), rows=D.rows;
  const L=window.LightweightCharts, charts={}, series={}, byDate=new Map(rows.map((r,i)=>[r.date,i]));
  const state={selected:rows.length-1, crosshairs:{}, actualCrosshairs:{}, visible:{}, errors:[], ready:false};
  const f=(v,n=2)=>v===null||v===undefined?'UNAVAILABLE':typeof v==='boolean'?(v?'YES':'NO'):typeof v==='string'?v:Number(v).toLocaleString('en-IN',{maximumFractionDigits:n,minimumFractionDigits:n});
  const val=(r,k)=>k.split('.').reduce((o,p)=>o?.[p],r);
  function make(id){
    const el=document.getElementById(id);
    const c=L.createChart(el,{width:el.clientWidth,height:el.clientHeight,
      layout:{background:{type:'solid',color:'#101722'},textColor:'#b7c4d5',attributionLogo:false},
      grid:{vertLines:{color:'#1d2a3a'},horzLines:{color:'#1d2a3a'}},
      rightPriceScale:{minimumWidth:95,borderColor:'#334154'},
      timeScale:{borderColor:'#334154',timeVisible:false,fixLeftEdge:true,fixRightEdge:true},
      crosshair:{mode:L.CrosshairMode.Normal},localization:{locale:'en-GB'},
      handleScroll:true,handleScale:true});
    charts[id]=c;
    new ResizeObserver(()=>{c.resize(el.clientWidth,el.clientHeight);drawWindows();}).observe(el);
    return c;
  }
  const price=make('price'),dss=make('dss'),volume=make('volume'),rs=make('rs');
  series.price=price.addSeries(L.CandlestickSeries,{upColor:'#26a69a',downColor:'#ef5350',borderVisible:false,wickUpColor:'#26a69a',wickDownColor:'#ef5350',priceLineVisible:false});
  series.price.setData(rows.map(r=>({time:r.date,open:r.open,high:r.high,low:r.low,close:r.close})));
  function line(chart,key,color,options={}){
    const s=chart.addSeries(L.LineSeries,{color,lineWidth:2,priceLineVisible:false,lastValueVisible:true,...options});
    s.setData(rows.map(r=>val(r,key)===null||val(r,key)===undefined?{time:r.date}:{time:r.date,value:val(r,key)}));
    series[key]=s;return s;
  }
  line(price,'ema10','#589dff');line(price,'ema20','#f3a947');
  line(dss,'dss_pct','#b47bea',{priceFormat:{type:'custom',formatter:v=>f(v,2)+'%',minMove:0.01}});
  series.volume=volume.addSeries(L.HistogramSeries,{priceLineVisible:false,priceFormat:{type:'volume'}});
  series.volume.setData(rows.map(r=>r.simple.original_final_color===null?{time:r.date}:{time:r.date,value:r.volume,color:r.simple.original_final_color}));
  line(volume,'simple.volume_sma','#d7cba8',{priceFormat:{type:'volume'}});
  series.volume.priceScale().applyOptions({scaleMargins:{top:.3,bottom:.08}});
  line(rs,'rs55','#54bca8',{priceFormat:{type:'price',precision:4,minMove:0.0001}});
  const zeroLine=series.rs55.createPriceLine({price:0,color:'#b172a8',lineWidth:1,lineStyle:L.LineStyle.Dashed,axisLabelVisible:true,title:'0'});
  const profileSeries=[];
  for(const key of ['vah','val']){
    const s=price.addSeries(L.LineSeries,{color:key==='vah'?'#d4ae68':'#8ea5c9',lineVisible:false,pointMarkersVisible:true,pointMarkersRadius:3,priceLineVisible:false,lastValueVisible:false,visible:false,title:'Approx '+key.toUpperCase()});
    s.setData(rows.map(r=>r.profile?.[key]===undefined||r.profile?.[key]===null?{time:r.date}:{time:r.date,value:r.profile[key]}));profileSeries.push(s);
  }
  const ev=D.events.filter(e=>e.plotted_session).map(e=>({time:e.plotted_session,position:'aboveBar',shape:'arrowDown',color:'#90b7e5',text:e.text+(e.date===e.plotted_session?'':' → '+e.plotted_session)}));
  L.createSeriesMarkers(series.price,ev);
  const pp=rows.filter(r=>r.simple.pocket_pivot===true).map(r=>({time:r.date,position:'aboveBar',shape:'circle',color:'#2196f3',text:'PP'}));
  for(const item of [...D.labels.high,...D.labels.low]) pp.push({time:item.bar_start_ist.slice(0,10),position:'aboveBar',shape:'square',color:'#d7cba8',text:item.text});
  L.createSeriesMarkers(series.volume,pp.sort((a,b)=>a.time.localeCompare(b.time)));
  const anchors={price:series.price,dss:series.dss_pct,volume:series.volume,rs:series.rs55};
  const anchorKey={price:'close',dss:'dss_pct',volume:'volume',rs:'rs55'};
  let syncing=false;
  function select(i){
    state.selected=Math.max(0,Math.min(rows.length-1,i));const r=rows[state.selected],v=r.simple;
    document.getElementById('inspection').textContent=`${D.symbol} · ${r.date} | O ${f(r.open)} H ${f(r.high)} L ${f(r.low)} C ${f(r.close)} | Δ ${f(r.change)} (${f(r.change_pct)}%) | Vol ${f(r.volume,0)} | EMA10 ${f(r.ema10)} EMA20 ${f(r.ema20)}`;
    document.getElementById('dtr-box').innerHTML=`${r.date} · SOURCE_VALIDATED<br>DTR ${f(r.daily_range)} INR · ATR14 RMA ${f(r.atr14)} INR<br>DTR / ATR ${f(r.dtr_atr_pct,0)}% · source RVol20 ${f(r.source_rvol20_pct,0)}%`;
    document.getElementById('dss-value').textContent=`${r.date} · RVol20 ${f(r.dss_pct,4)}% · daily behaviorally reconstructed · first20 local sessions unavailable`;
    const events=['pocket_pivot','dry','high_up','high_down','original_hve','original_hvy','original_hvq'].map(k=>`${k.replace('original_','')}: ${f(v[k])}`).join(' · ');
    document.getElementById('volume-value').textContent=`${r.date} · Vol ${f(r.volume,0)} · SMA21 ${f(v.volume_sma)} · RVOL21 ${f(v.rvol_pct,0)}% · U/D20 ${f(v.up_down_table_value,1)} · turnover50 ${f(v.turnover_crores_estimate)} crore | ${events} | color ${v.original_final_color??'UNAVAILABLE (record context/warmup)'}`;
    document.getElementById('rs-value').textContent=`${r.date} · RS55 ${f(r.rs55,6)} · ${r.rs55_status} | Approximate VAH ${f(r.profile?.vah)} / VAL ${f(r.profile?.val)} INR (${r.profile_status})`;
    state.crosshairs={};
    for(const [id,c] of Object.entries(charts)){
      const v=val(r,anchorKey[id]);
      // Use a real zero-baseline coordinate only to position the missing panel's
      // crosshair. Never inject a zero data point or tooltip value.
      c.setCrosshairPosition(v??0,r.date,anchors[id]);state.crosshairs[id]=r.date;
    }
  }
  for(const [id,c] of Object.entries(charts)){
    c.subscribeCrosshairMove(p=>{
      if(p.time)state.actualCrosshairs[id]=typeof p.time==='string'?p.time:`${p.time.year}-${String(p.time.month).padStart(2,'0')}-${String(p.time.day).padStart(2,'0')}`;
      if(syncing||!p.time)return;
      const t=typeof p.time==='string'?p.time:`${p.time.year}-${String(p.time.month).padStart(2,'0')}-${String(p.time.day).padStart(2,'0')}`;
      if(!byDate.has(t))return;
      syncing=true;select(byDate.get(t));syncing=false;
    });
    c.timeScale().subscribeVisibleLogicalRangeChange(range=>{
      if(syncing||!range)return;syncing=true;
      for(const [other,target] of Object.entries(charts))if(other!==id)target.timeScale().setVisibleLogicalRange(range);
      syncing=false;drawWindows();
    });
  }
  function drawWindows(){
    const svg=document.getElementById('windows-overlay');if(!svg||!charts.price)return;
    const selectedWindow=document.getElementById('window').value;
    const w=selectedWindow===''?null:D.windows[Number(selectedWindow)];
    if(!w){svg.innerHTML='';return;}
    const a=price.timeScale().timeToCoordinate(w.observed_first_date),b=price.timeScale().timeToCoordinate(w.observed_last_date);
    if(a===null||b===null){svg.innerHTML='';return;}
    const max=svg.clientWidth-95,lo=Math.max(0,a),hi=Math.min(max,b);
    svg.innerHTML=hi<lo?'':`<rect x="${lo}" y="0" width="${Math.max(1,hi-lo)}" height="315" fill="#71849c" opacity=".07"/><path d="M${a} 0V315 M${b} 0V315" stroke="#8c9fb7" stroke-dasharray="5 4"/><text x="${lo+8}" y="310" fill="#9cacc0" font-size="11">APPROXIMATE · first/last observed sessions</text>`;
  }
  function range(first,last){
    const from=byDate.get(first),to=byDate.get(last);if(from===undefined||to===undefined)return;
    price.timeScale().setVisibleLogicalRange({from:from-2,to:to+2});drawWindows();
  }
  const definitions=[['ema10','EMA10',D.statuses.ema],['ema20','EMA20',D.statuses.ema],['dtr','DTR / ATR',D.statuses.dtr],['dss','DSS RVol',D.statuses.dss],['volume','Simple Volume',D.statuses.simple],['rs','RS55',D.statuses.rs],['profile','VAH / VAL',D.statuses.profile]];
  for(const [id,title,status] of definitions){
    const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.id='toggle-'+id;input.checked=id!=='profile';input.disabled=id==='profile'&&status==='UNAVAILABLE';
    label.append(input,document.createTextNode(title+' '));const small=document.createElement('small');small.textContent=status;label.append(small);document.getElementById('controls').append(label);
    function apply(){
      state.visible[id]=input.checked;
      if(id==='ema10'||id==='ema20')series[id].applyOptions({visible:input.checked});
      else if(id==='dtr')document.getElementById('dtr-box').hidden=!input.checked;
      else if(id==='profile')profileSeries.forEach(s=>s.applyOptions({visible:input.checked}));
      else document.getElementById(id+'-panel').hidden=!input.checked;
    }
    input.addEventListener('change',apply);apply();
  }
  const win=document.getElementById('window');
  D.windows.forEach((w,i)=>{const o=document.createElement('option');o.value=String(i);o.textContent=`${w.event_id} · ${w.observed_first_date} → ${w.observed_last_date}`;win.append(o);});
  win.addEventListener('change',()=>{if(win.value!==''){const w=D.windows[Number(win.value)];range(w.observed_first_date,w.observed_last_date);}else drawWindows();});
  function step(d){syncing=true;select(state.selected+d);syncing=false;}
  document.getElementById('prev').onclick=()=>step(-1);document.getElementById('next').onclick=()=>step(1);
  document.addEventListener('keydown',e=>{if(e.target.tagName==='SELECT')return;if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();step(e.key==='ArrowLeft'?-1:1);}});
  document.getElementById('reset').onclick=()=>{win.value='';price.timeScale().fitContent();drawWindows();};
  document.getElementById('recent').onclick=()=>range(rows[Math.max(0,rows.length-125)].date,rows.at(-1).date);
  const missingColors=rows.filter(r=>r.simple.original_final_color===null).length;
  if(missingColors===rows.length){const notice=document.createElement('div');notice.className='color-availability';notice.textContent='Histogram colors UNAVAILABLE: insufficient validated record history. Actual volume remains in candle inspection; SMA21 shown where available.';document.getElementById('volume-panel').append(notice);}
  document.getElementById('provenance').textContent=`${rows.length} retained Upstox daily candles · ${rows[0].date} to ${rows.at(-1).date}. ${missingColors} final histogram colors unavailable (warmup/record context); no fallback classification. ${D.record_context}. ${D.profile_note} ${D.availability_note}`;
  syncing=true;select(rows.length-1);syncing=false;
  if(D.initial){const i=D.windows.findIndex(w=>w.observed_first_date===D.initial.first&&w.observed_last_date===D.initial.last);if(i>=0)win.value=String(i);range(D.initial.first,D.initial.last);}else {if(D.windows.length)win.value='0';price.timeScale().fitContent();drawWindows();}
  state.ready=true;
  // Exposed chart API for reproducible integration checks, not financial math.
  window.ER03C={data:D,charts,series,state,zeroLine,select:i=>step(i-state.selected),range,drawWindows};
})();
