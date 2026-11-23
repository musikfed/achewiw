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
  $('wbExportStructure').disabled=!baseImage||!figures.length;
  $('wbExportSvg').disabled=!baseImage||!figures.length;
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
// R3.7 phase 1: export manually verified primitives as real editable SVG/JSON.
// Deliberately do not include raster pixels or claim automatic image understanding.
function structuredDrawing(){
  if(!baseImage||!figures.length)throw new Error('Сначала нарисуй и проверь фигуры схемы.');
  const width=canvas.width,height=canvas.height;
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1||
     width*height>MAX_CANVAS_PIXELS)throw new Error('Недопустимый размер схемы.');
  const elements=figures.map((item,index)=>{
    if(!validAnnotation(item))throw new Error('Неподдерживаемый объект №'+(index+1));
    const {type,x,y,w,h}=item;
    const color=/^#[0-9a-f]{6}$/i.test(item.color||'')?item.color:'#176d47';
    const stroke=Number.isFinite(item.stroke)?Math.max(1,Math.min(30,item.stroke)):3;
    const fontSize=Number.isFinite(item.fontSize)?
      Math.max(10,Math.min(100,item.fontSize)):26;
    return {id:'element-'+(index+1),type,x,y,w,h,color,stroke,
      ...(type==='text'?{text:item.text||'',fontSize}:{})};
  });
  return {format:'achewiw-diagram',version:1,mode:'manual-review-required',
    source:'manual-canvas-overlay',canvas:{width,height},elements};
}
function xmlText(value){
  return String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&apos;');
}
function svgFromStructure(structure){
  const {width,height}=structure.canvas;
  const out=[`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`,
    '<title>Редактируемая схема ACHEWIW — ручная разметка</title>'];
  for(const obj of structure.elements){
    const {type,x,y,w,h,color,stroke}=obj;
    const style=`stroke="${color}" stroke-width="${stroke}" fill="none" stroke-linecap="round" stroke-linejoin="round"`;
    if(type==='rectangle')out.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" ${style}/>`);
    else if(type==='cover')out.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="white"/>`);
    else if(type==='ellipse')out.push(`<ellipse cx="${x+w/2}" cy="${y+h/2}" rx="${Math.max(1,Math.abs(w/2))}" ry="${Math.max(1,Math.abs(h/2))}" ${style}/>`);
    else if(type==='line'||type==='arrow'){
      out.push(`<line x1="${x}" y1="${y}" x2="${x+w}" y2="${y+h}" ${style}/>`);
      if(type==='arrow'){
        const angle=Math.atan2(h,w),size=Math.max(10,stroke*3),ex=x+w,ey=y+h;
        const p1=[ex-size*Math.cos(angle-.46),ey-size*Math.sin(angle-.46)];
        const p2=[ex-size*Math.cos(angle+.46),ey-size*Math.sin(angle+.46)];
        out.push(`<polygon points="${ex},${ey} ${p1[0]},${p1[1]} ${p2[0]},${p2[1]}" fill="${color}"/>`);
      }
    }else if(type==='text'){
      // Canvas uses top-aligned glyphs; SVG baseline is slightly offset.
      out.push(`<text x="${x}" y="${y+obj.fontSize}" font-family="Arial, sans-serif" font-size="${obj.fontSize}" font-weight="600" fill="${color}">${xmlText(obj.text)}</text>`);
    }
  }
  out.push('</svg>');
  return out.join('\n');
}
$('wbExportStructure').addEventListener('click',()=>{
  try{
    const graph=structuredDrawing();
    downloadBlob(new Blob([JSON.stringify(graph,null,2)],{type:'application/json'}),
      'ACHEWIW-diagram-'+(selected+1)+'-editable.json');
    status('Структура схемы выгружена в JSON. Это ручная разметка, а не распознавание рисунка.');
  }catch(e){status(e.message,true);}
});
$('wbExportSvg').addEventListener('click',()=>{
  try{
    const graph=structuredDrawing();
    downloadBlob(new Blob([svgFromStructure(graph)],{type:'image/svg+xml;charset=utf-8'}),
      'ACHEWIW-diagram-'+(selected+1)+'-editable.svg');
    status('Редактируемый SVG выгружен. В нём только созданные вручную фигуры и текст, без фонового растра.');
  }catch(e){status(e.message,true);}
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
