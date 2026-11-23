/* ACHEWIW R3.6 — small local image/diagram workbench.
   Intentionally NOT Photoshop: background PNG/JPEG/WebP, vector-like overlay
   figures and text, undo/redo, opt-in replacement. No cloud or auto changes.
   Loaded before docwork.js, connected to its private R3 session via a tiny API.
*/
(()=>{
'use strict';
const $=id=>document.getElementById(id);
let host=null, selected=-1, baseImage=null, baseBlob=null, external=false;
let vectorSource=false, figures=[], history=[], futures=[], editStart=null;
let down=null, moving=-1, movingOriginal=null, dirty=false, operationBusy=false;
let baseUrl=null, originalUrl=null, compareUrl=null, generation=0;
const MAX_OPS=100, MAX_STEPS=60, MAX_CANVAS_PIXELS=16000000;
const ANNOTATION_KEY_PREFIX='achewiw:docwork:r361:canvas:';
const canvas=$('wbCanvas'), ctx=canvas.getContext('2d',{alpha:true});
const status=(text,isError=false)=>{
  $('wbStatus').textContent=text;
  $('wbStatus').style.color=isError?'#a6322f':'';
};
function cleanupUrl(key){
  const url=({baseUrl,originalUrl,compareUrl})[key];
  if(url)URL.revokeObjectURL(url);
  if(key==='baseUrl')baseUrl=null;
  else if(key==='originalUrl')originalUrl=null;
  else compareUrl=null;
}
function changeButtons(){
  $('wbUndo').disabled=!history.length;
  $('wbRedo').disabled=!futures.length;
  $('wbApply').disabled=selected<0||!baseImage||operationBusy;
  $('wbDownload').disabled=selected<0||!baseImage;
  $('wbExportSvg').disabled=selected<0||!baseImage||!figures.length;
  $('wbExportGraph').disabled=selected<0||!baseImage||!figures.length;
  $('wbImportGraph').disabled=!exportScope()||operationBusy;
  $('wbVisionFile').disabled=!exportScope()||operationBusy;
  $('wbVisionPreflight').disabled=!exportScope()||operationBusy;
  $('wbVisionAnalyze').disabled=!exportScope()||operationBusy||!$('wbVisionModel').value;
  $('wbAuditGraph').disabled=selected<0||!baseImage||!figures.length;
  $('wbUseReplacement').disabled=selected<0||!host?.assetFor(selected);
}
function annotationKey(){
  // No access token is stored. A draft ID and a single picture determine scope.
  // External uploaded files and temporary vector preview have no stable base.
  if(external||vectorSource||selected<0||!baseImage)return null;
  const draft=host?.current()?.draft;
  if(!draft?.id)return null;
  const asset=host?.assetFor(selected)||'original';
  return ANNOTATION_KEY_PREFIX+draft.id+':'+selected+':'+asset;
}
function saveAnnotations(){
  const key=annotationKey();
  if(!key)return;
  try{
    if(!dirty||!figures.length)localStorage.removeItem(key);
    else localStorage.setItem(key,JSON.stringify({version:1,figures}));
  }catch{
    status('Разметка не сохранилась в браузере: не хватает места или хранилище отключено.',true);
  }
}
function validAnnotation(obj){
  if(!obj||!['text','rectangle','cover','ellipse','line','arrow'].includes(obj.type))return false;
  if(!['x','y','w','h'].every(k=>Number.isFinite(obj[k])&&Math.abs(obj[k])<=MAX_CANVAS_PIXELS))return false;
  if(obj.type==='text'&&(typeof obj.text!=='string'||obj.text.length>180))return false;
  if(obj.color!=null&&!/^#[0-9a-f]{6}$/i.test(obj.color))return false;
  return true;
}
function loadAnnotations(){
  const key=annotationKey();
  if(!key)return;
  try{
    const raw=localStorage.getItem(key);
    if(!raw)return;
    const saved=JSON.parse(raw);
    if(saved?.version!==1||!Array.isArray(saved.figures)||
       saved.figures.length>MAX_OPS||!saved.figures.every(validAnnotation)){
      status('Сохранённая разметка повреждена — не загружена.',true);
      return;
    }
    figures=saved.figures;dirty=figures.length>0;
    history=[];futures=[];render();changeButtons();
    if(dirty)status('Несохранённая разметка восстановлена из этого браузера. Примени замену, чтобы закрепить её в книге.');
  }catch{status('Не удалось восстановить локальную разметку.',true);}
}
function record(){
  history.push(JSON.stringify(figures));
  if(history.length>MAX_STEPS)history.shift();
  futures=[];
  dirty=true;changeButtons();
}
function restoreHistory(redo=false){
  const from=redo?futures:history,to=redo?history:futures;
  if(!from.length)return;
  to.push(JSON.stringify(figures));
  figures=JSON.parse(from.pop());
  dirty=true;render();
  changeButtons();saveAnnotations();
}
function clearDrawing(){
  clearVisionReview();
  $('wbGraphAudit').hidden=true;
  figures=[];history=[];futures=[];dirty=false;
  down=null;moving=-1;changeButtons();
}
function render(){
  ctx.clearRect(0,0,canvas.width,canvas.height);
  if(baseImage)ctx.drawImage(baseImage,0,0,canvas.width,canvas.height);
  for(const [index,obj] of figures.entries()){
    ctx.save();
    ctx.lineWidth=obj.stroke||3;
    ctx.strokeStyle=obj.color||'#176d47';
    ctx.fillStyle=obj.color||'#176d47';
    ctx.lineJoin='round';ctx.lineCap='round';
    if(obj.type==='text'){
      ctx.font='600 '+(obj.fontSize||26)+'px Arial, sans-serif';
      ctx.textBaseline='top';
      ctx.fillText(obj.text||'',obj.x,obj.y);
    } else if(obj.type==='rectangle'||obj.type==='cover'){
      if(obj.type==='cover'){ctx.fillStyle='#fff';ctx.fillRect(obj.x,obj.y,obj.w,obj.h);}
      else ctx.strokeRect(obj.x,obj.y,obj.w,obj.h);
    } else if(obj.type==='ellipse'){
      ctx.beginPath();ctx.ellipse(obj.x+obj.w/2,obj.y+obj.h/2,
        Math.max(1,Math.abs(obj.w/2)),Math.max(1,Math.abs(obj.h/2)),
        0,0,2*Math.PI);ctx.stroke();
    } else if(obj.type==='line'||obj.type==='arrow'){
      ctx.beginPath();ctx.moveTo(obj.x,obj.y);
      ctx.lineTo(obj.x+obj.w,obj.y+obj.h);ctx.stroke();
      if(obj.type==='arrow'){
        const angle=Math.atan2(obj.h,obj.w);
        const size=Math.max(10,obj.stroke*3);
        ctx.beginPath();ctx.moveTo(obj.x+obj.w,obj.y+obj.h);
        ctx.lineTo(obj.x+obj.w-size*Math.cos(angle-0.46),
                   obj.y+obj.h-size*Math.sin(angle-0.46));
        ctx.lineTo(obj.x+obj.w-size*Math.cos(angle+0.46),
                   obj.y+obj.h-size*Math.sin(angle+0.46));
        ctx.closePath();ctx.fill();
      }
    }
    if(index===moving){
      ctx.setLineDash([8,5]);
      ctx.strokeStyle='#ff8b20';ctx.lineWidth=2;
      ctx.strokeRect(Math.min(obj.x,obj.x+obj.w)-5,
        Math.min(obj.y,obj.y+obj.h)-5,Math.abs(obj.w)+10,Math.abs(obj.h)+10);
    }
    ctx.restore();
  }
  // Preview image is deliberately a separate scaled snapshot, not the source.
  if(compareUrl){cleanupUrl('compareUrl');}
  if(baseImage){
    try{
      const reduced=document.createElement('canvas');
      const scale=Math.min(1,600/canvas.width,400/canvas.height);
      reduced.width=Math.max(1,Math.round(canvas.width*scale));
      reduced.height=Math.max(1,Math.round(canvas.height*scale));
      reduced.getContext('2d').drawImage(canvas,0,0,reduced.width,reduced.height);
      reduced.toBlob(blob=>{
        if(!blob||!baseImage)return;
        cleanupUrl('compareUrl');
        compareUrl=URL.createObjectURL(blob);
        $('wbCurrentPreview').src=compareUrl;
        $('wbCurrentPreview').hidden=false;
      },'image/png');
    }catch{}
  }
}
function imageFromBlob(blob){
  return new Promise((resolve,reject)=>{
    const url=URL.createObjectURL(blob),img=new Image();
    img.onload=()=>{
      if(img.naturalWidth*img.naturalHeight>MAX_CANVAS_PIXELS){
        URL.revokeObjectURL(url);
        reject(new Error('Изображение больше 16 млн пикселей — уменьши отдельно'));
        return;
      }
      resolve({img,url});
    };
    img.onerror=()=>{URL.revokeObjectURL(url);reject(new Error('Файл изображения нельзя открыть'));};
    img.src=url;
  });
}
async function setCanvasBlob(blob,isExternal=false,fromVector=false){
  if(dirty&&figures.length&&!window.confirm('На холсте есть несохранённые фигуры. Загрузить другое изображение?'))return;
  saveAnnotations();
  const {img,url}=await imageFromBlob(blob);
  cleanupUrl('baseUrl');baseUrl=url;baseImage=img;baseBlob=blob;
  external=isExternal;vectorSource=fromVector;
  canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;
  clearDrawing();render();
  status((isExternal?'Внешнее изображение загружено. ':
    fromVector?'Загружен результат трассировки. ':'Исходный рисунок открыт. ')+
    img.naturalWidth+'×'+img.naturalHeight+
    '. Изменения не попадут в книгу, пока не нажмёшь «Применить замену».');
}
function data(){
  const session=host?.current();
  if(!session?.draft||session.report?.type!=='docx')
    throw new Error('Сначала открой DOCX и создай защищённый черновик');
  return session;
}
async function requestImage(path){
  const {draft}=data();
  const response=await fetch('/api/docwork/drafts/'+draft.id+path,{
    headers:{'X-ACHEWIW-Docwork-Token':draft.token},cache:'no-store'
  });
  if(!response.ok)throw new Error('Не удалось прочитать изображение (HTTP '+response.status+')');
  return await response.blob();
}
async function originalFor(index,initialize=false){
  const blob=await requestImage('/images/'+index+'/original');
  if(initialize||!originalUrl){
    cleanupUrl('originalUrl');
    const image=await imageFromBlob(blob);
    originalUrl=image.url;
    $('wbOriginalPreview').src=originalUrl;
    $('wbOriginalPreview').hidden=false;
  }
  return blob;
}
async function choose(index,mode='original'){
  const current=data();
  if(!current.report?.images?.some(x=>x.index===index))
    throw new Error('Рисунок не найден');
  const serial=++generation;
  if(index!==selected&&dirty&&figures.length&&
     !window.confirm('Фигуры на холсте не применены. Сменить рисунок?'))return;
  saveAnnotations();
  selected=index;
  $('wbPicture').value=String(index);
  clearDrawing();
  cleanupUrl('originalUrl');
  try{
    const original=await originalFor(index,true);
    if(serial!==generation)return;
    if(mode==='applied'&&host.assetFor(index)){
      const replacement=await requestImage('/assets/'+host.assetFor(index));
      if(serial!==generation)return;
      await setCanvasBlob(replacement,false,false);
    }else await setCanvasBlob(original,false,false);
    if(serial!==generation)return;
    loadAnnotations();
    changeButtons();
  }catch(e){status(e.message,true);}
}
function toCanvas(event){
  const r=canvas.getBoundingClientRect();
  return {x:(event.clientX-r.left)*canvas.width/r.width,
          y:(event.clientY-r.top)*canvas.height/r.height};
}
function number(id,low,high,fallback){
  const n=Number($(id).value);
  return Number.isFinite(n)?Math.max(low,Math.min(high,n)):fallback;
}
function newShape(type,start,end){
  const base={type,color:$('wbColor').value,
    stroke:number('wbLineWidth',1,30,4),x:start.x,y:start.y,
    w:end.x-start.x,h:end.y-start.y};
  if(['rectangle','cover','ellipse'].includes(type)){
    base.x=Math.min(start.x,end.x);
    base.y=Math.min(start.y,end.y);
    base.w=Math.abs(end.x-start.x);base.h=Math.abs(end.y-start.y);
    if(base.w<2||base.h<2)return null;
  }
  return base;
}
function hit(obj,p){
  const margin=20;
  if(obj.type==='text'){
    const font=obj.fontSize||26;
    return p.x>=obj.x-margin&&p.x<=obj.x+(obj.text||'').length*font*.65+margin&&
      p.y>=obj.y-margin&&p.y<=obj.y+font*1.5+margin;
  }
  const left=Math.min(obj.x,obj.x+obj.w),top=Math.min(obj.y,obj.y+obj.h);
  return p.x>=left-margin&&p.x<=left+Math.abs(obj.w)+margin&&
    p.y>=top-margin&&p.y<=top+Math.abs(obj.h)+margin;
}
canvas.addEventListener('pointerdown',event=>{
  if(!baseImage||event.button!==0)return;
  const p=toCanvas(event),tool=$('wbTool').value;
  canvas.setPointerCapture(event.pointerId);
  if(tool==='text'){
    if(figures.length>=MAX_OPS){status('До 100 фигур на иллюстрацию',true);return;}
    record();figures.push({type:'text',x:p.x,y:p.y,w:0,h:0,
      text:$('wbText').value.slice(0,180),color:$('wbColor').value,
      fontSize:number('wbTextSize',10,100,28)});
    render();saveAnnotations();return;
  }
  if(tool==='select'){
    moving=-1;
    for(let i=figures.length-1;i>=0;i--){
      if(hit(figures[i],p)){moving=i;break;}
    }
    if(moving<0)return;
    record();
    movingOriginal={...figures[moving]};down=p;
    return;
  }
  down=p;
});
canvas.addEventListener('pointermove',event=>{
  if(!down||!baseImage)return;
  const p=toCanvas(event);
  if(moving>=0&&movingOriginal){
    figures[moving].x=movingOriginal.x+(p.x-down.x);
    figures[moving].y=movingOriginal.y+(p.y-down.y);
    render();return;
  }
  const tool=$('wbTool').value;
  if(!['rectangle','ellipse','line','arrow','cover'].includes(tool))return;
  const ghost=newShape(tool,down,p);
  if(!ghost)return;
  // Ghost is not a committed step; show in canvas until pointerup.
  const current=figures;
  figures=[...current,ghost];render();figures=current;
});
canvas.addEventListener('pointerup',event=>{
  if(!down||!baseImage){moving=-1;return;}
  const p=toCanvas(event);
  if(moving>=0){
    if(movingOriginal&&Math.abs(figures[moving].x-movingOriginal.x)<1&&
       Math.abs(figures[moving].y-movingOriginal.y)<1){
      history.pop();
    }
    dirty=true;moving=-1;movingOriginal=null;down=null;
    render();changeButtons();saveAnnotations();return;
  }
  const tool=$('wbTool').value;
  if(['rectangle','ellipse','line','arrow','cover'].includes(tool)&&figures.length<MAX_OPS){
    const shape=newShape(tool,down,p);
    if(shape){record();figures.push(shape);}
  }
  down=null;render();saveAnnotations();
});
canvas.addEventListener('pointercancel',()=>{
  down=null;moving=-1;movingOriginal=null;render();
});
$('wbUndo').addEventListener('click',()=>restoreHistory(false));
$('wbRedo').addEventListener('click',()=>restoreHistory(true));
$('wbUseOriginal').addEventListener('click',async()=>{
  try{await setCanvasBlob(await originalFor(selected),false,false);}
  catch(e){status(e.message,true);}
});
$('wbUseReplacement').addEventListener('click',async()=>{
  try{
    const id=host.assetFor(selected);
    if(!id)throw new Error('Заменённого изображения ещё нет');
    await setCanvasBlob(await requestImage('/assets/'+id),false,false);
  }catch(e){status(e.message,true);}
});
$('wbExternal').addEventListener('change',async()=>{
  const file=$('wbExternal').files?.[0];
  if(!file)return;
  try{
    if(!/\.(png|jpe?g|webp)$/i.test(file.name)||file.size>10*1024*1024)
      throw new Error('Разрешены PNG/JPEG/WebP размером до 10 МиБ');
    await setCanvasBlob(file,true,false);
  }catch(e){status(e.message,true);}
  $('wbExternal').value='';
});
$('wbAutoOutline').addEventListener('click',async()=>{
  if(selected<0||operationBusy)return;
  if(external){
    status('Трассировка R3.5 пока берёт исходную картинку DOCX. '
      +'Чтобы не перепутать её с загруженным файлом, операция остановлена. '
      +'Внешнюю схему можно править здесь вручную или подготовить SVG отдельно.',true);
    return;
  }
  operationBusy=true;
  status('Трассировка схемы локально через Potrace. Мелкий текст может исказиться.');
  try{
    const {draft}=data();
    const response=await fetch('/api/docwork/drafts/'+draft.id+
        '/images/'+selected+'/vectorize',{
      method:'POST',cache:'no-store',
      headers:{'X-ACHEWIW-Docwork-Token':draft.token,'Content-Type':'application/json'},
      body:JSON.stringify({colors:Number($('wbTraceColors').value),output:'svg',scale:2})
    });
    if(!response.ok){
      const result=await response.json().catch(()=>({}));
      throw new Error(result.detail||'Не удалось трассировать SVG (HTTP '+response.status+')');
    }
    const svg=await response.blob();
    await setCanvasBlob(svg,false,true);
    status('Векторные контуры загружены для сравнения. Буквы не стали редактируемым '
      +'текстом: при необходимости закрой старые подписи и набери новые.');
  }catch(e){status(e.message,true);}
  finally{operationBusy=false;changeButtons();}
});
function canvasPng(){
  return new Promise((resolve,reject)=>{
    canvas.toBlob(blob=>blob?resolve(blob):
      reject(new Error('Не удалось подготовить PNG')),'image/png');
  });
}
function downloadBlob(blob,name){
  const url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=name;document.body.append(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(url),60000);
}
// R3.7 stage A: a verifiable, editable vector annotation layer.
// No OCR/VLM inference: source raster is deliberately absent from exports.
function xmlText(value){
  return String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&apos;');
}
function round(value){
  return Math.round(value*100)/100;
}
function structuredShapes(){
  return figures.filter(validAnnotation).map((obj,index)=>({
    id:'shape-'+(index+1),type:obj.type,
    x:round(obj.x),y:round(obj.y),w:round(obj.w),h:round(obj.h),
    color:obj.color||'#176d47',
    stroke:Math.max(1,Math.min(30,Number(obj.stroke)||3)),
    ...(obj.type==='text'?{text:obj.text||'',fontSize:Math.max(10,Math.min(100,Number(obj.fontSize)||26))}:{})
  }));
}
function svgFromShapes(shapes){
  const parts=[
    '<svg xmlns="http://www.w3.org/2000/svg" width="'+canvas.width+'" height="'+canvas.height+
      '" viewBox="0 0 '+canvas.width+' '+canvas.height+'">',
    '<title>ACHEWIW R3.7 — только проверенная ручная разметка</title>',
    '<desc>Исходное изображение не встроено; автоматического распознавания текста или связей нет.</desc>'
  ];
  for(const obj of shapes){
    const attrs=' id="'+obj.id+'" stroke="'+xmlText(obj.color)+'" stroke-width="'+obj.stroke+
      '" stroke-linecap="round" stroke-linejoin="round"';
    if(obj.type==='text'){
      parts.push('<text id="'+obj.id+'" x="'+obj.x+'" y="'+obj.y+
        '" fill="'+xmlText(obj.color)+'" font-size="'+obj.fontSize+
        '" font-family="Arial, sans-serif" font-weight="600" dominant-baseline="text-before-edge">'+
        xmlText(obj.text)+'</text>');
    }else if(obj.type==='rectangle'){
      parts.push('<rect'+attrs+' fill="none" x="'+obj.x+'" y="'+obj.y+
        '" width="'+Math.abs(obj.w)+'" height="'+Math.abs(obj.h)+'"/>');
    }else if(obj.type==='cover'){
      parts.push('<rect id="'+obj.id+'" fill="#ffffff" x="'+obj.x+'" y="'+obj.y+
        '" width="'+Math.abs(obj.w)+'" height="'+Math.abs(obj.h)+'"/>');
    }else if(obj.type==='ellipse'){
      parts.push('<ellipse'+attrs+' fill="none" cx="'+(obj.x+obj.w/2)+'" cy="'+
        (obj.y+obj.h/2)+'" rx="'+Math.max(1,Math.abs(obj.w/2))+'" ry="'+
        Math.max(1,Math.abs(obj.h/2))+'"/>');
    }else if(obj.type==='line'||obj.type==='arrow'){
      const x2=obj.x+obj.w,y2=obj.y+obj.h;
      parts.push('<line'+attrs+' x1="'+obj.x+'" y1="'+obj.y+'" x2="'+x2+'" y2="'+y2+'"/>');
      if(obj.type==='arrow'){
        const angle=Math.atan2(obj.h,obj.w),size=Math.max(10,obj.stroke*3);
        const p1x=x2-size*Math.cos(angle-0.46),p1y=y2-size*Math.sin(angle-0.46);
        const p2x=x2-size*Math.cos(angle+0.46),p2y=y2-size*Math.sin(angle+0.46);
        parts.push('<path id="'+obj.id+'-head" fill="'+xmlText(obj.color)+'" d="M '+
          x2+' '+y2+' L '+p1x+' '+p1y+' L '+p2x+' '+p2y+' Z"/>');
      }
    }
  }
  return parts.join('\n')+'\n</svg>\n';
}
// R3.7C: browser-local review; every decision is tied to exact geometry.
const LINK_REVIEW_PREFIX='achewiw:docwork:r37c:links:';
function linkReviewKey(){
  const key=annotationKey();
  return key?LINK_REVIEW_PREFIX+key:null;
}
function geometrySignature(){
  return JSON.stringify(structuredShapes().map(({id,type,x,y,w,h})=>({id,type,x,y,w,h})));
}
function readLinkDecisions(){
  const key=linkReviewKey();
  if(!key)return {};
  try{
    const saved=JSON.parse(localStorage.getItem(key)||'null');
    if(saved?.version!==1||saved.signature!==geometrySignature()||
       !saved.decisions||typeof saved.decisions!=='object')return {};
    return Object.fromEntries(Object.entries(saved.decisions)
      .filter(([id,v])=>/^shape-\d+$/.test(id)&&
        (v==='accepted'||v==='rejected')).slice(0,100));
  }catch{return {};}
}
function saveLinkDecisions(decisions){
  const key=linkReviewKey();
  if(!key){status('Для сохранения решений открой исходный рисунок DOCX, не внешний файл.',true);return false;}
  try{
    localStorage.setItem(key,JSON.stringify({
      version:1,signature:geometrySignature(),decisions
    }));
    return true;
  }catch{
    status('Не удалось сохранить подтверждения: локальное хранилище недоступно.',true);
    return false;
  }
}
const LABEL_REVIEW_PREFIX='achewiw:docwork:r37d:labels:';
function labelReviewKey(){
  const key=annotationKey();
  return key?LABEL_REVIEW_PREFIX+key:null;
}
function labelSignature(){
  return JSON.stringify(structuredShapes().map(({id,type,x,y,w,h,text})=>
    ({id,type,x,y,w,h,text:text||''})));
}
function readLabelAssignments(){
  const key=labelReviewKey();
  if(!key)return {};
  try{
    const saved=JSON.parse(localStorage.getItem(key)||'null');
    if(saved?.version!==1||saved.signature!==labelSignature()||
       !saved.assignments||typeof saved.assignments!=='object')return {};
    return saved.assignments;
  }catch{return {};}
}
function saveLabelAssignments(assignments){
  const key=labelReviewKey();
  if(!key){
    status('Для сохранения подписей открой исходный рисунок DOCX.',true);
    return false;
  }
  try{
    localStorage.setItem(key,JSON.stringify({
      version:1,signature:labelSignature(),assignments
    }));
    return true;
  }catch{
    status('Не удалось сохранить назначения подписей в браузере.',true);
    return false;
  }
}
function exportScope(){
  const draft=host?.current()?.draft;
  if(!draft?.id||selected<0||!baseImage||external||vectorSource)return null;
  return {draftId:draft.id,pictureIndex:selected,
    baseAsset:host.assetFor(selected)||'original',
    width:canvas.width,height:canvas.height};
}
function auditGraph(){
  const checker=window.ACHEWIWDiagramGraph;
  if(!checker)throw Error('Модуль проверки геометрии не загружен');
  return checker.applyLabelReviews(
    checker.applyReviews(checker.inspect(structuredShapes()),readLinkDecisions()),
    readLabelAssignments());
}
function showLinkAudit(){
  const panel=$('wbGraphAudit');
  try{
    const audit=auditGraph();
    panel.replaceChildren();
    const heading=document.createElement('strong');
    heading.textContent='R3.7C · Ручная проверка связей (без распознавания смысла)';
    const summary=document.createElement('p');
    summary.textContent='Узлов: '+audit.summary.nodes+
      ' · линий/стрелок: '+audit.summary.edges+
      ' · подтверждено: '+audit.reviewSummary.confirmed+
      ' · отклонено: '+audit.reviewSummary.rejected+
      ' · ожидают проверки: '+audit.reviewSummary.pending+'.';
    panel.append(heading,summary);
    const list=document.createElement('ul');
    for(const edge of audit.edges){
      const item=document.createElement('li');
      const description=document.createElement('span');
      description.textContent=edge.id+' · '+(edge.type==='arrow'?'стрелка':'линия')+
        ' · начало: '+(edge.fromCandidates.join(', ')||'вне узлов')+
        ' · конец: '+(edge.toCandidates.join(', ')||'вне узлов')+
        ' · '+(edge.reviewDecision==='accepted'?'подтверждено человеком':
          edge.reviewDecision==='rejected'?'отклонено':'не подтверждено');
      item.append(description);
      if(edge.status==='candidate'){
        for(const [decision,label] of [
          ['accepted','✓ Подтвердить'],['rejected','✕ Отклонить'],['pending','Сбросить решение']
        ]){
          const button=document.createElement('button');
          button.type='button';button.textContent=label;
          button.disabled=edge.reviewDecision===decision;
          button.addEventListener('click',()=>{
            const updated=readLinkDecisions();
            if(decision==='pending')delete updated[edge.id];
            else updated[edge.id]=decision;
            if(saveLinkDecisions(updated))showLinkAudit();
          });
          item.append(' ',button);
        }
      }else{
        const reason=document.createElement('small');
        reason.textContent=' · Неоднозначно ('+edge.issues.join(', ')+
          '): исправь геометрию перед подтверждением.';
        item.append(reason);
      }
      list.append(item);
    }
    panel.append(list);
    const labelHeading=document.createElement('strong');
    labelHeading.textContent='R3.7D · Назначение подписей узлам';
    panel.append(labelHeading);
    const labelsList=document.createElement('ul');
    for(const label of audit.labels){
      const item=document.createElement('li');
      const caption=document.createElement('span');
      caption.textContent=label.id+' · «'+label.text+'» → ';
      const select=document.createElement('select');
      select.setAttribute('aria-label','Узел для надписи '+label.id);
      const empty=document.createElement('option');
      empty.value='';empty.textContent='Не назначено';
      select.append(empty);
      for(const node of audit.nodes){
        const option=document.createElement('option');
        option.value=node.id;
        option.textContent=node.id+' ('+node.type+')';
        select.append(option);
      }
      select.value=label.nodeId||'';
      select.addEventListener('change',()=>{
        const decisions=readLabelAssignments();
        if(select.value)decisions[label.id]=select.value;
        else delete decisions[label.id];
        if(saveLabelAssignments(decisions))showLinkAudit();
      });
      item.append(caption,select);
      labelsList.append(item);
    }
    panel.append(labelsList);
    const count=document.createElement('p');
    count.textContent='Ручных назначений: '+audit.labelReviewSummary.assigned+
      ' · без назначения: '+audit.labelReviewSummary.pending+'.';
    panel.append(count);
    const note=document.createElement('p');
    note.textContent='Решения сохраняются только в этом браузере для текущего черновика и геометрии. Экспортируй JSON для переноса. Исходный DOCX не меняется.';
    panel.append(note);panel.hidden=false;
  }catch(error){
    panel.replaceChildren();
    panel.textContent='Проверка схемы не выполнена: '+error.message;
    panel.hidden=false;
  }
}
$('wbAuditGraph').addEventListener('click',showLinkAudit);
$('wbExportSvg').addEventListener('click',()=>{
  if(!baseImage||!figures.length)return;
  const shapes=structuredShapes();
  downloadBlob(new Blob([svgFromShapes(shapes)],{type:'image/svg+xml;charset=utf-8'}),
    'ACHEWIW-diagram-'+(selected+1)+'-editable-overlay.svg');
  status('Экспортирован редактируемый SVG только с ручными фигурами. Фоновое изображение не входит в SVG.');
});
$('wbExportGraph').addEventListener('click',()=>{
  if(!baseImage||!figures.length)return;
  const scope=exportScope();
  if(!scope){status('JSON с привязкой к книге доступен только для исходного рисунка DOCX.',true);return;}
  const schema={format:'achewiw-verified-diagram-layer',version:1,
    documentScope:scope,
    canvas:{width:canvas.width,height:canvas.height},
    origin:'manual',recognition:'not-performed',sourceRasterEmbedded:false,
    shapes:structuredShapes(),geometryAudit:auditGraph(),
    reviewStorage:'browser-local; export JSON to preserve decisions'};
  downloadBlob(new Blob([JSON.stringify(schema,null,2)+'\n'],{type:'application/json'}),
    'ACHEWIW-diagram-'+(selected+1)+'-structure.json');
  status('Структура JSON сохранена. Все объекты добавлены вручную; распознавания ИИ не было.');
});

$('wbVisionPreflight').addEventListener('click',async()=>{
  const scope=exportScope();
  if(!scope)return status('Сначала выбери исходный рисунок DOCX.',true);
  const draft=host.current().draft;
  const button=$('wbVisionPreflight'),modelSelect=$('wbVisionModel');
  button.disabled=true;
  modelSelect.replaceChildren(new Option('Проверяем Ollama…',''));
  modelSelect.disabled=true;
  status('Проверяем установленные модели и аренду GPU; загрузки модели пока нет.');
  try{
    const response=await fetch('/api/docwork/drafts/'+encodeURIComponent(draft.id)+
      '/vision/preflight',{
        headers:{'X-ACHEWIW-Docwork-Token':draft.token},cache:'no-store'
      });
    if(!response.ok)throw Error('HTTP '+response.status);
    const result=await response.json();
    if(JSON.stringify(exportScope())!==JSON.stringify(scope))return;
    modelSelect.replaceChildren(new Option('Выбери модель',''));
    if(Array.isArray(result.models))for(const name of result.models){
      if(typeof name!=='string')continue;
      modelSelect.add(new Option(name,name));
    }
    modelSelect.disabled=!result.ready;
    status('Ollama Vision: '+result.reason+'. Доступные модели: '+
      (result.models?.join(', ')||'нет')+
      '. Запуск — отдельной кнопкой по твоему выбору.');
  }catch(error){
    modelSelect.replaceChildren(new Option('Проверка недоступна',''));
    status('Проверка Ollama Vision: '+error.message,true);
  }finally{changeButtons();}
});
$('wbVisionModel').addEventListener('change',changeButtons);
$('wbVisionAnalyze').addEventListener('click',async()=>{
  const scope=exportScope(),draft=host?.current()?.draft;
  const model=$('wbVisionModel').value;
  if(!scope||!draft||!model||operationBusy)return;
  if(!confirm('Отправить текущий ОРИГИНАЛЬНЫЙ рисунок DOCX локальной модели '+
     model+'? Может использовать GPU RTX 3060. Оригинал книги не изменится.'))return;
  operationBusy=true;changeButtons();
  status('Распознаём один рисунок локальной Ollama. Кандидаты потребуют ручного принятия.');
  try{
    const response=await fetch('/api/docwork/drafts/'+encodeURIComponent(draft.id)+
      '/vision/analyze',{
        method:'POST',cache:'no-store',
        headers:{'Content-Type':'application/json','X-ACHEWIW-Docwork-Token':draft.token},
        body:JSON.stringify({image_index:scope.pictureIndex,model,
          width:scope.width,height:scope.height,base_asset:'original',consent:true})
      });
    const result=await response.json().catch(()=>({}));
    if(!response.ok)throw Error(result.detail||('HTTP '+response.status));
    if(JSON.stringify(exportScope())!==JSON.stringify(scope))
      throw Error('За время распознавания выбран другой рисунок. Результат отброшен.');
    const parser=window.ACHEWIWVisionProposals;
    const verified=parser.validate(result,scope);
    clearVisionReview();
    visionReview={proposals:verified.proposals,scopeKey:JSON.stringify(scope),
      decisions:Object.create(null)};
    showVisionReview();
    status('Получено '+verified.count+' НЕПРОВЕРЕННЫХ предложений. Принять можно по одному.');
  }catch(error){status('Vision: '+error.message,true);}
  finally{operationBusy=false;changeButtons();}
});
// Vision suggestions are never a drawing layer until individually accepted.
let visionReview=null;
function clearVisionReview(){
  visionReview=null;
  $('wbVisionReview').replaceChildren();
  $('wbVisionReview').hidden=true;
}
function showVisionReview(){
  const panel=$('wbVisionReview');
  panel.replaceChildren();
  if(!visionReview){panel.hidden=true;return;}
  const scope=exportScope();
  if(!scope||JSON.stringify(scope)!==visionReview.scopeKey){
    clearVisionReview();return;
  }
  const heading=document.createElement('strong');
  heading.textContent='R3.7G · Предложения Vision ('+visionReview.proposals.length+')';
  panel.append(heading);
  const info=document.createElement('p');
  info.textContent='Каждую фигуру проверяй отдельно. Принятие добавляет только ручной объект на холст, не изменяя исходный DOCX.';
  panel.append(info);
  const list=document.createElement('ol');
  for(const proposal of visionReview.proposals){
    const item=document.createElement('li');
    const state=visionReview.decisions[proposal.id]||'pending';
    const title=document.createElement('span');
    title.textContent=proposal.id+' · '+proposal.type+' · уверенность '+
      Math.round(proposal.confidence*100)+'% · '+
      (proposal.type==='text'?'«'+proposal.text+'» · ':'')+
      [proposal.x,proposal.y,proposal.w,proposal.h].join(', ')+
      ' · '+(state==='accepted'?'добавлено':state==='rejected'?'отклонено':'требует проверки');
    item.append(title);
    if(state==='pending'){
      const accept=document.createElement('button');
      accept.type='button';accept.textContent='✓ Принять фигуру';
      accept.addEventListener('click',()=>{
        if(!visionReview||JSON.stringify(exportScope())!==visionReview.scopeKey)return clearVisionReview();
        if(visionReview.decisions[proposal.id])return;
        if(figures.length>=MAX_OPS)return status('Достигнут лимит 100 фигур',true);
        let shape;
        try{shape=window.ACHEWIWVisionProposals.toManualShape(proposal,exportScope());}
        catch(e){status('Фигура отклонена: '+e.message,true);return;}
        if(!confirm('Добавить проверенную фигуру '+proposal.id+' на ручной холст?\n'
          +'Это НЕ применяет замену в DOCX.'))return;
        record();figures.push(shape);
        visionReview.decisions[proposal.id]='accepted';
        render();changeButtons();saveAnnotations();showVisionReview();
      });
      const reject=document.createElement('button');
      reject.type='button';reject.textContent='✕ Отклонить';
      reject.addEventListener('click',()=>{
        if(!visionReview||JSON.stringify(exportScope())!==visionReview.scopeKey)return clearVisionReview();
        visionReview.decisions[proposal.id]='rejected';showVisionReview();
      });
      item.append(' ',accept,' ',reject);
    }
    list.append(item);
  }
  panel.append(list);
  const warning=document.createElement('p');
  warning.textContent='Отклонение и список предложений живут только до смены рисунка/F5. Принятые фигуры сохраняются как обычная ручная разметка (R3.6.1), доступен Undo. SVG/JSON экспортируются отдельно.';
  panel.append(warning);panel.hidden=false;
}
$('wbVisionFile').addEventListener('change',async()=>{
  const input=$('wbVisionFile'),file=input.files?.[0];
  input.value='';
  if(!file)return;
  clearVisionReview();
  try{
    const scope=exportScope();
    if(!scope)throw Error('Доступен только исходный рисунок текущего DOCX');
    if(file.size>256*1024)throw Error('JSON превышает 256 КиБ');
    const parser=window.ACHEWIWVisionProposals;
    if(!parser)throw Error('Модуль проверки не загружен');
    const review=parser.validate(JSON.parse(await file.text()),scope);
    if(JSON.stringify(exportScope())!==JSON.stringify(scope))
      throw Error('Рисунок изменился во время чтения файла');
    visionReview={proposals:review.proposals,scopeKey:JSON.stringify(scope),
      decisions:Object.create(null)};
    showVisionReview();
    status('Предложения доступны для поштучной проверки, автоматически ничего не применено.');
  }catch(e){
    clearVisionReview();status('Предложения отклонены: '+e.message,true);
  }
});
$('wbImportGraph').addEventListener('change',async()=>{
  const input=$('wbImportGraph'),file=input.files?.[0];
  input.value='';
  if(!file)return;
  try{
    if(!exportScope())throw Error('Открой исходный рисунок DOCX');
    if(file.size>256*1024)throw Error('JSON больше 256 КиБ');
    const raw=await file.text();
    const decoder=window.ACHEWIWDiagramImport;
    if(!decoder)throw Error('Модуль импорта не загружен');
    const validated=decoder.validate(JSON.parse(raw),exportScope());
    if(!confirm('Заменить ТОЛЬКО текущую ручную разметку ('+
      validated.shapes.length+' фигур) данными из JSON? Исходный DOCX останется прежним.'))return;
    // Keep previous vector layer accessible through Undo.
    record();
    figures=validated.shapes;
    dirty=true;
    render();changeButtons();
    saveAnnotations();
    // Reviews are scoped by the restored geometry, never by stale positions.
    if(!saveLinkDecisions(validated.decisions)||!saveLabelAssignments(validated.assignments))
      status('Фигуры восстановлены; часть подтверждений не сохранилась в браузере.',true);
    else status('Разметка восстановлена из JSON. Изменение оригинального DOCX не выполнялось.');
    $('wbGraphAudit').hidden=true;
  }catch(error){
    status('Импорт отклонён: '+error.message,true);
  }
});
$('wbDownload').addEventListener('click',async()=>{
  if(!baseImage)return;
  try{downloadBlob(await canvasPng(),
    'ACHEWIW-illustration-'+(selected+1)+'-for-Photoshop.png');}
  catch(e){status(e.message,true);}
});
$('wbApply').addEventListener('click',async()=>{
  if(!baseImage||selected<0||operationBusy)return;
  operationBusy=true;changeButtons();
  try{
    const {draft}=data(),index=selected;
    // Preserve exact original pixels on direct external replacement when
    // there are no added shapes; composition uses high-resolution PNG otherwise.
    const payload=external&&!figures.length?baseBlob:await canvasPng();
    if(payload.size>10*1024*1024)
      throw new Error('PNG для сохранения превышает 10 МиБ. Уменьши рисунок отдельно.');
    const form=new FormData();
    form.append('file',payload,'revised-picture-'+(index+1)+'.png');
    const response=await fetch('/api/docwork/drafts/'+draft.id+'/assets',{
      method:'POST',headers:{'X-ACHEWIW-Docwork-Token':draft.token},
      body:form,cache:'no-store'
    });
    if(!response.ok){
      const answer=await response.json().catch(()=>({}));
      throw new Error(answer.detail||'Не удалось сохранить замену (HTTP '+response.status+')');
    }
    const asset=await response.json();
    const previousAnnotationKey=annotationKey();
    host.applyAsset(index,asset.id);
    status('Новая версия PNG сохранена отдельно (ID '+asset.id.slice(0,8)+
      '). Для включения в DOCX нажми «Сохранить изменения» и экспортируй копию. '
      +'Оригинал останется прежним.');
    // The accepted asset already contains these drawn figures. A reopened
    // replacement must never superimpose them a second time.
    if(previousAnnotationKey)try{localStorage.removeItem(previousAnnotationKey);}catch{}
    dirty=false;changeButtons();
  }catch(e){status(e.message,true);}
  finally{operationBusy=false;changeButtons();}
});
$('wbRestore').addEventListener('click',async()=>{
  if(selected<0)return;
  const index=selected;
  if(dirty&&figures.length&&!window.confirm('На холсте есть несохранённая разметка. Снять замену?'))return;
  saveAnnotations();
  clearDrawing();
  host.restoreAsset(index);
  try{await choose(index,'original');}catch(e){status(e.message,true);}
  status('Замена снята для следующего экспорта. Предыдущие версии доступны в истории R3.');
});
window.addEventListener('pagehide',saveAnnotations);
$('wbBack').addEventListener('click',()=>{
  saveAnnotations();
  $('imageWorkbench').hidden=true;
  $('structurePane').hidden=false;
  $('pdfPane').hidden=true;
  $('openImageWorkbench').classList.remove('selected');
  $('openImageWorkbench').setAttribute('aria-pressed','false');
  $('showStructure').classList.add('selected');
  $('showStructure').setAttribute('aria-pressed','true');
});
$('wbPicture').addEventListener('change',()=>{
  const index=Number($('wbPicture').value);
  if(Number.isInteger(index)&&index>=0){
    const wasSelected=host.current().selected===index;
    host.selectPicture(index);
    if(wasSelected)void choose(index,host.assetFor(index)?'applied':'original');
  }
});
$('openImageWorkbench').addEventListener('click',()=>{
  if(!host?.current()?.draft)return;
  $('structurePane').hidden=true;$('pdfPane').hidden=true;
  $('imageWorkbench').hidden=false;
  $('openImageWorkbench').classList.add('selected');
  $('openImageWorkbench').setAttribute('aria-pressed','true');
  $('showStructure').classList.remove('selected');
  $('showStructure').setAttribute('aria-pressed','false');
  const list=host.current().report?.images||[];
  $('wbPicture').replaceChildren();
  for(const item of list){
    if(!Number.isInteger(item.width_px))continue;
    const option=document.createElement('option');
    option.value=String(item.index);
    option.textContent='Рисунок №'+(item.index+1)+' · '+item.width_px+
      '×'+item.height_px+(host.assetFor(item.index)?' · заменён':'');
    $('wbPicture').append(option);
  }
  const requested=host.current().selected;
  const first=list.find(x=>Number.isInteger(x.width_px));
  if(first)void choose(Number.isInteger(requested)?requested:first.index,
     host.assetFor(Number.isInteger(requested)?requested:first.index)?'applied':'original');
  else status('В DOCX не найдено подходящих встроенных растровых рисунков.',true);
});
window.ACHEWIWWorkbench={
  connect(api){host=api;},
  refresh(){
    const good=host?.current()?.report?.type==='docx'&&
      host.current()?.report?.images?.length>0;
    $('openImageWorkbench').disabled=!good;
  },
  reset(){
    ++generation;selected=-1;baseImage=null;baseBlob=null;
    cleanupUrl('baseUrl');cleanupUrl('originalUrl');cleanupUrl('compareUrl');
    clearDrawing();
    $('imageWorkbench').hidden=true;
    $('openImageWorkbench').classList.remove('selected');
    $('wbExternal').value='';
    status('Загрузи DOCX и выбери рисунок, чтобы начать работу.');
  },
  selectionChanged(index){
    if($('imageWorkbench').hidden||selected===index)return;
    void choose(index,host.assetFor(index)?'applied':'original');
  }
};
})();
