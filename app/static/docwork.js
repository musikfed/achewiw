(() => {
'use strict';
const $ = id => document.getElementById(id);
let source = null, report = null, selected = null, previewUrl = null, rendered = 80, processing = false;
const overrides = Object.create(null), replacements = Object.create(null);
const imageEdits = Object.create(null);
const imageAssets = Object.create(null);
const imageMoves = Object.create(null);
const paragraphAlignments = Object.create(null);
let activeParagraph = null;
const undoStack = [], redoStack = [];
const MAX_UNDO_STEPS = 200;
const MAX_UNDO_CHARS = 5_000_000;
let lastUndoKey = '', lastUndoTime = 0;
let imageMoveCounter = 0;
let canvasLimit = 120;
let selectedImage = null;
const imageThumbUrls = new Map();
let vectorPreviewUrl = null, vectorBusy = false;

let draft = null, draftSaveTimer = null, draftSaveBusy = false, draftConflicted = false;
let lastSavedState = '{}';
let recoveredLocalScratch = null;
const DRAFT_KEY = 'achewiw-docwork-r3-current';
const SCRATCH_KEY = 'achewiw-docwork-r3-pending';
const FONT_CHOICES=[
  'Times New Roman','Arial','Calibri',
  'Liberation Serif','Liberation Sans','Liberation Mono',
  'DejaVu Serif','DejaVu Sans','DejaVu Sans Mono',
  'Carlito','Caladea','FreeSerif','FreeSans','FreeMono'
];
function populateFontSelects() {
  for(const id of ['bodyFont','chapterFont','subchapterFont']){
    const select=$(id);
    for(const name of FONT_CHOICES){
      const option=document.createElement('option');
      option.value=name;option.textContent=name;
      select.append(option);
    }
  }
}
populateFontSelects();
function applyCatalogFont(sourceId) {
  const target=$('fontApplyTarget').value;
  const font=$(sourceId).value;
  if(!FONT_CHOICES.includes(font)||!['bodyFont','chapterFont','subchapterFont'].includes(target))return;
  $(target).value=font;
  $(target).dispatchEvent(new Event('change',{bubbles:true}));
}
$('applyStandardFont').addEventListener('click',()=>applyCatalogFont('fontStandardChoices'));
$('applyOtherFont').addEventListener('click',()=>applyCatalogFont('fontOtherChoices'));
function toggleBar(id,buttonId,storageKey){
  const bar=$(id),button=$(buttonId);
  const compact=!bar.classList.contains('is-compact');
  bar.classList.toggle('is-compact',compact);
  button.textContent=compact?'Показать панель ↓':'Скрыть панель ↑';
  button.setAttribute('aria-expanded',String(!compact));
  try{sessionStorage.setItem(storageKey,compact?'1':'0');}catch{}
}
for(const [id,buttonId,key] of [
  ['workshopActions','toggleActionBar','achewiw-docwork-actionbar-compact'],
  ['editToolbar','toggleEditToolbar','achewiw-docwork-editbar-compact']
]){
  $(buttonId).addEventListener('click',()=>toggleBar(id,buttonId,key));
  try{
    if(sessionStorage.getItem(key)==='1')toggleBar(id,buttonId,key);
  }catch{}
}
function radiusLimit(item){
  return item?.corner_max_percent??24;
}
function controlledRadius(item,amount){
  const value=Number(amount);
  return Math.max(0,Math.min(radiusLimit(item),Number.isFinite(value)?Math.round(value*2)/2:0));
}
function cornerPixelRadius(img,percent){
  const width=img.getBoundingClientRect().width;
  const item=(report?.images||[]).find(x=>x.index===Number(img.dataset.imageIndex));
  if(!item)return;
  const displayedHeight=width * item.height_px/item.width_px;
  img.style.borderRadius=(Math.min(width,displayedHeight)*controlledRadius(item,percent)/100).toFixed(2)+'px';
}
function toggleSidebar(){
  const root=$('workshopColumns'),button=$('toggleSidebar');
  const wide=!root.classList.contains('editor-wide');
  root.classList.toggle('editor-wide',wide);
  button.textContent=wide?'Показать настройки →':'Скрыть настройки ←';
  button.setAttribute('aria-expanded',String(!wide));
  try{sessionStorage.setItem('achewiw-docwork-sidebar-hidden',wide?'1':'0');}catch{}
}
$('toggleSidebar').addEventListener('click',toggleSidebar);
try{if(sessionStorage.getItem('achewiw-docwork-sidebar-hidden')==='1')toggleSidebar();}catch{}
const settingsIds = {
  preset:'preset', output_name:'outputName', auto_headings:'autoHeadings',
  clear_highlights:'clearHighlights', hide_proofing:'hideProofing', heading_bold:'headingBold',
  body_font:'bodyFont', chapter_font:'chapterFont', subchapter_font:'subchapterFont',
  body_size:'bodySize', chapter_size:'chapterSize', subchapter_size:'subchapterSize'
};
function canonicalState(value) {
  if (Array.isArray(value)) return '['+value.map(canonicalState).join(',')+']';
  if (value && typeof value==='object')
    return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonicalState(value[k])).join(',')+'}';
  return JSON.stringify(value);
}
/* R3.3: bounded multi-step action timeline. Only changes to manuscript text,
   paragraph formatting and picture layout are undoable; binary source is never
   copied. Fast typing in the same paragraph is grouped into one action. */
function undoSnapshot() {
  return JSON.stringify({
    overrides:{...overrides},replacements:{...replacements},
    paragraphAlignments:{...paragraphAlignments},
    images:{...imageEdits},moves:{...imageMoves},artwork:{...imageAssets}
  });
}
function timelineStatus() {
  $('undoEdit').disabled=undoStack.length===0;
  $('redoEdit').disabled=redoStack.length===0;
  $('undoDepth').textContent=undoStack.length+' отмен · '+redoStack.length+
    ' повторов · до '+MAX_UNDO_STEPS+' шагов';
}
function limitUndoStack(stack) {
  while(stack.length>MAX_UNDO_STEPS)stack.shift();
  let sum=stack.reduce((size,snapshot)=>size+snapshot.length,0);
  while(sum>MAX_UNDO_CHARS && stack.length>1)sum-=stack.shift().length;
}
function resetUndoTimeline() {
  undoStack.length=0;redoStack.length=0;
  lastUndoKey='';lastUndoTime=0;activeParagraph=null;
  timelineStatus();
}
function editWithUndo(actionKey, mutation, coalesce=false) {
  if(!report)return;
  const before=undoSnapshot();
  mutation();
  const after=undoSnapshot();
  if(before===after)return;
  const now=Date.now();
  const sameTyping=coalesce&&lastUndoKey===actionKey&&
    now-lastUndoTime<850&&undoStack.length>0;
  if(!sameTyping)undoStack.push(before);
  limitUndoStack(undoStack);
  redoStack.length=0;
  lastUndoKey=actionKey;
  lastUndoTime=now;
  changeCount();markDraftDirty();timelineStatus();
}
function applyUndoSnapshot(data) {
  const state=JSON.parse(data);
  for(const [object,source] of [
    [overrides,state.overrides], [replacements,state.replacements],
    [paragraphAlignments,state.paragraphAlignments],
    [imageEdits,state.images], [imageMoves,state.moves],
    [imageAssets,state.artwork]
  ]) {
    for(const key of Object.keys(object))delete object[key];
    Object.assign(object,source||{});
  }
  imageMoveCounter=Math.max(0,...Object.values(imageMoves)
    .map(item=>Number(item?.order)||0));
  const previous=activeParagraph;
  changeCount();drawRows();renderDocumentCanvas();renderImageGallery();
  window.ACHEWIWWorkbench?.refresh();
  activeParagraph=previous;
  updateEditToolbar();
  markDraftDirty();
}
function undoEditAction(redo=false) {
  const from=redo?redoStack:undoStack;
  const to=redo?undoStack:redoStack;
  if(!from.length)return;
  const previous=from.pop();
  to.push(undoSnapshot());limitUndoStack(to);
  lastUndoKey='';lastUndoTime=0;
  applyUndoSnapshot(previous);
  timelineStatus();
  const area=activeParagraph===null?null:
    $('documentCanvas').querySelector(
      '.canvasText[data-paragraph-index="'+activeParagraph+'"]');
  if(area && area.tagName==='TEXTAREA') {
    area.focus({preventScroll:true});
    const pos=Math.min(area.value.length,area.selectionStart??area.value.length);
    area.setSelectionRange(pos,pos);
  }
}
function selectTextParagraph(index) {
  activeParagraph=index;
  updateEditToolbar();
}
function updateEditToolbar() {
  const row=report?.paragraphs?.find(item=>item.index===activeParagraph);
  const allowed=report?.type==='docx'&&!!row&&!!row.editable;
  $('activeParagraphLabel').textContent=allowed?
    'Абзац №'+(activeParagraph+1)+' · текст изменяется прямо на листе':
    'Нажми на текст для редактирования';
  const alignment=allowed?(paragraphAlignments[String(activeParagraph)]||
    row.alignment||'inherit'):'inherit';
  for(const id of ['alignLeft','alignCenter','alignRight','alignJustify','alignReset']) {
    const button=$(id);
    button.disabled=!allowed;
    button.setAttribute('aria-pressed',String(allowed&&button.dataset.align===alignment));
  }
  $('canvasRole').disabled=!allowed;
  $('canvasRole').value=allowed?(overrides[String(activeParagraph)]||'keep'):'keep';
}
function rememberDraft() {
  if (!draft) return;
  try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft)); } catch {}
}
function forgetDraft() {
  try { sessionStorage.removeItem(DRAFT_KEY); sessionStorage.removeItem(SCRATCH_KEY); } catch {}
}
function captureDraftState() {
  const settings = optionsForExport();
  delete settings.overrides; delete settings.replacements;
  delete settings.image_edits;
  delete settings.image_moves;
  delete settings.image_assets;
  return {
    settings, overrides:{...overrides}, replacements:{...replacements},
    images:{...imageEdits}, moves:{...imageMoves},
    artwork:{...imageAssets},alignments:{...paragraphAlignments},
    editor:{selected, text: selected === null ? '' : $('paragraphText').value,
            role: selected === null ? 'keep' : $('paragraphRole').value},
    ui:{search:$('paragraphSearch').value, filter:$('paragraphFilter').value, rendered,
        selectedImage, canvasLimit}
  };
}
function scratchDraft() {
  if (!draft || !report) return;
  try { sessionStorage.setItem(SCRATCH_KEY, JSON.stringify({
    id:draft.id, revision:draft.revision, state:captureDraftState()
  })); } catch { $('draftStatus').textContent = 'Локальная страховочная копия не поместилась в браузер. Нажмите «Сохранить правки сейчас».'; }
}
function draftControls() {
  for (const id of ['saveDraft','quickSaveDraft','saveCanvasNow','reloadDraft','exportDraftState','closeDraft'])
    $(id).disabled = !draft;
  $('restoreDraftVersion').disabled = !draft || !$('draftHistory').value;
  $('draftHistory').disabled = !draft;
}
function draftMessage(text, error=false) {
  $('draftStatus').textContent = text;
  $('draftStatus').classList.toggle('error',error);
  let state='idle',short='○ Черновик ещё не открыт';
  if (/Конфликт|другой вкладке/i.test(text)){state='conflict';short='⚠ Конфликт версий';}
  else if (error||/не сохранены|не удалось|Ошибка/i.test(text)){state='error';short='⚠ Не сохранено';}
  else if (/Сохраняем/i.test(text)){state='saving';short='◷ Сохранение на сервере…';}
  else if (/несохранённые|запланировано/i.test(text)){state='dirty';short='● Есть несохранённые правки';}
  else if (/сохранён|Загружена серверная редакция|Исходный файл сохранён/i.test(text)){
    state='saved';short='✓ Сохранено в SQLite · версия №'+(draft?.revision??'?');
  }
  $('saveIndicator').dataset.saveState=state;
  $('saveIndicator').textContent=short;
  $('canvasSaveState').textContent=short;
}
function applyDraftState(state) {
  if (!state || !report) return;
  resetUndoTimeline();
  for (const obj of [overrides,replacements,imageEdits,imageMoves,imageAssets,paragraphAlignments])
    for (const key of Object.keys(obj)) delete obj[key];
  Object.assign(overrides,state.overrides || {});
  Object.assign(replacements,state.replacements || {});
  Object.assign(imageEdits,state.images || {});
  Object.assign(imageMoves,state.moves || {});
  Object.assign(imageAssets,state.artwork || {});
  Object.assign(paragraphAlignments,state.alignments || {});
  imageMoveCounter=Math.max(0,...Object.values(imageMoves)
    .map(x=>Number(x?.order)||0));
  canvasLimit=Math.max(120,Math.min(3000,Number(state.ui?.canvasLimit)||120));
  for (const [key,id] of Object.entries(settingsIds)) {
    if (!Object.prototype.hasOwnProperty.call(state.settings||{},key)) continue;
    const element=$(id), value=state.settings[key];
    if (element.type === 'checkbox') element.checked=!!value;
    else element.value=value===null ? '' : value;
  }
  if (state.settings) {
    $('pdfTextOnly').checked=state.settings.pdf_mode==='text_only';
    $('pdfLossConfirmed').checked=state.settings.confirm_text_loss===true;
  }
  $('paragraphSearch').value=String(state.ui?.search || '');
  $('paragraphFilter').value=state.ui?.filter==='all'?'all':'headings';
  rendered=Math.max(80,Math.min(3000,Number(state.ui?.rendered)||80));
  clearPdf();
  $('changeCounter').textContent =
    (new Set(Object.keys(overrides).concat(Object.keys(replacements),
       Object.keys(paragraphAlignments))).size
    + new Set(Object.keys(imageEdits).concat(Object.keys(imageMoves),Object.keys(imageAssets))).size)
      +' ручных правок';
  selected=null; $('editPane').hidden=true; drawRows();
  renderImageGallery();
  renderDocumentCanvas();
  updateEditToolbar();
  window.ACHEWIWWorkbench?.refresh();
  const imageIndex = state.ui?.selectedImage;
  if (Number.isInteger(imageIndex)) selectImage(imageIndex);
  if (Number.isInteger(state.editor?.selected)) {
    editParagraph(state.editor.selected);
    if (selected!==null) {
      const row=report.paragraphs?.find(x=>x.index===selected);
      if (row?.editable && typeof state.editor.text==='string')
        $('paragraphText').value=state.editor.text;
      if (['keep','heading1','heading2','body'].includes(state.editor.role))
        $('paragraphRole').value=state.editor.role;
    }
  }
}
function markDraftDirty() {
  if (!draft || !report) return;
  if (lastNormativeReport) {
    resetNormativeReport();
    $('normativeStatus').textContent='Документ изменён после проверки; прежний отчёт недействителен. Нажми «Проверить DOCX» снова.';
  }
  scratchDraft();
  if (canonicalState(captureDraftState())===lastSavedState && !draftConflicted) {
    draftMessage('Черновик уже сохранён · редакция №'+draft.revision);
    return;
  }
  if (draftConflicted) {
    draftMessage('Конфликт: в другой вкладке уже сохранена новая редакция. Ваши локальные изменения пока не перезаписываются. Скачайте свои правки JSON.',true);
    return;
  }
  draftMessage('Есть несохранённые изменения · сохранение запланировано…');
  clearTimeout(draftSaveTimer);
  draftSaveTimer=setTimeout(()=>{void saveDraftNow();},900);
}
async function saveDraftNow() {
  if (!draft || !report || draftConflicted) return;
  clearTimeout(draftSaveTimer);
  if (draftSaveBusy) return;
  if (canonicalState(captureDraftState())===lastSavedState) {
    draftMessage('Черновик уже сохранён · редакция №'+draft.revision);
    return;
  }
  draftSaveBusy=true;
  const id=draft.id, revision=draft.revision, state=captureDraftState();
  const serial=JSON.stringify(state);
  draftMessage('Сохраняем правки на сервере…');
  try {
    const response=await fetch('/api/docwork/drafts/'+id,{
      method:'PUT',cache:'no-store',
      headers:{'Content-Type':'application/json','X-ACHEWIW-Docwork-Token':draft.token},
      body:JSON.stringify({revision,state})
    });
    const result=await response.json();
    if (response.status===409) {
      draftConflicted=true;
      scratchDraft();
      throw new Error(result.detail||'Другая вкладка сохранила новую редакцию');
    }
    if (!response.ok) throw new Error(result.detail||'HTTP '+response.status);
    if (!draft || draft.id!==id) return;
    draft.revision=result.revision; rememberDraft();
    lastSavedState=canonicalState(state);
    scratchDraft();
    if (JSON.stringify(captureDraftState()) !== serial) {
      markDraftDirty();
    } else {
      draftMessage('Черновик сохранён · редакция №'+result.revision+' · '+result.updated_at);
    }
  } catch(e) {
    draftMessage('Правки не сохранены: '+e.message+
      '. Локальная копия в этой вкладке остаётся; можно повторить сохранение.',true);
  } finally {
    draftSaveBusy=false;
    // Capture edits arriving while a network request was in flight.
    if (draft && draft.id===id && !draftConflicted &&
        JSON.stringify(captureDraftState())!==serial)
      markDraftDirty();
  }
}
async function draftFetch(path) {
  const response=await fetch('/api/docwork/drafts/'+draft.id+path,{
    cache:'no-store',headers:{'X-ACHEWIW-Docwork-Token':draft.token}
  });
  if (!response.ok) {
    let detail='HTTP '+response.status;
    try { detail=(await response.json()).detail||detail; } catch {}
    throw new Error(detail);
  }
  return response;
}
async function refreshDraftHistory() {
  if (!draft) return;
  try {
    const versions=await (await draftFetch('/history')).json();
    const select=$('draftHistory'); select.replaceChildren();
    const title=document.createElement('option'); title.value='';
    title.textContent='Выбрать сохранённую редакцию'; select.append(title);
    for (const item of versions) {
      const option=document.createElement('option');option.value=String(item.revision);
      option.textContent='№'+item.revision+' · '+item.saved_at;select.append(option);
    }
    draftControls();
  } catch(e) { draftMessage('Не удалось прочитать историю: '+e.message,true); }
}
async function restoreDraft() {
  let stored=null;
  try { stored=JSON.parse(sessionStorage.getItem(DRAFT_KEY)||'null'); } catch {}
  if (!stored?.id || !stored?.token) return;
  draft=stored; draftControls();draftMessage('Восстанавливаем сохранённую редакцию после F5…');
  try {
    const meta=await (await draftFetch('')).json();
    const blob=await (await draftFetch('/source')).blob();
    source=new File([blob],meta.filename,{type:meta.filename.toLowerCase().endsWith('.pdf')?
      'application/pdf':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'});
    reset();
    draft.revision=meta.revision; rememberDraft();
    lastSavedState=canonicalState(meta.state);
    $('fileLabel').textContent=meta.filename+' · восстановлено с сервера';
    await inspect(true);
    if (!report) throw new Error('Не удалось заново прочитать структуру документа');
    applyDraftState(meta.state);
    let pending=null;
    try { pending=JSON.parse(sessionStorage.getItem(SCRATCH_KEY)||'null'); } catch {}
    if (pending?.id===draft.id && pending.revision===draft.revision) {
      if (canonicalState(pending.state)!==canonicalState(meta.state)) {
        applyDraftState(pending.state); markDraftDirty();
        draftMessage('Восстановлены изменения перед F5 · синхронизация с сервером…');
      }
    } else if (pending?.id===draft.id && pending.revision < draft.revision &&
               canonicalState(pending.state)!==canonicalState(meta.state)) {
      recoveredLocalScratch=pending;
      draftMessage('После F5 найдены более старые несохранённые локальные изменения. '
        +'Они НЕ перезаписали сервер. Сначала скачайте локальные правки JSON, затем решите, как объединить версии.',true);
    }
    await refreshDraftHistory();
    if (!draftSaveTimer) draftMessage('Редактор восстановлен · редакция №'+draft.revision);
  } catch(e) {
    draftMessage('Восстановление не удалось: '+e.message+
      '. Ключ черновика сохранён в этой вкладке, исходный файл не удалён.',true);
  }
}

function clearVectorPreview(){
  if(vectorPreviewUrl){URL.revokeObjectURL(vectorPreviewUrl);vectorPreviewUrl=null;}
  $('vectorPreview').hidden=true;
  $('vectorPreview').removeAttribute('src');
}
async function exportTracedPicture(output){
  if(!draft||!report||selectedImage===null||vectorBusy)return;
  const index=selectedImage, id=draft.id;
  const colors=Number($('vectorColors').value);
  vectorBusy=true;
  $('downloadVectorSvg').disabled=true;
  $('downloadVectorPng').disabled=true;
  $('vectorStatus').textContent='Трассируем схему локально на сервере (без GPU)…';
  try{
    const response=await fetch('/api/docwork/drafts/'+id+'/images/'+index+'/vectorize',{
      method:'POST',cache:'no-store',
      headers:{'Content-Type':'application/json','X-ACHEWIW-Docwork-Token':draft.token},
      body:JSON.stringify({colors,output,scale:2})
    });
    if(!response.ok){
      let message='HTTP '+response.status;
      try{message=(await response.json()).detail||message;}catch{}
      throw new Error(message);
    }
    const blob=await response.blob();
    if(draft?.id!==id || selectedImage!==index)return;
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url;a.download='ACHEWIW-diagram-'+(index+1)+'-trace.'+output;
    document.body.append(a);a.click();a.remove();
    if(output==='svg'){
      clearVectorPreview();vectorPreviewUrl=url;
      $('vectorPreview').src=url;$('vectorPreview').hidden=false;
    } else {setTimeout(()=>URL.revokeObjectURL(url),2000);}
    $('vectorStatus').textContent='Подготовлен '+output.toUpperCase()+
      ' только для сравнения. Исходная картинка и DOCX не изменены. '
      +'Проверь текст, стрелки, границы и цвета перед использованием.';
  }catch(error){
    $('vectorStatus').textContent='Трассировка не завершена: '+error.message+
      '. Оригинал не изменён.';
  }finally{
    vectorBusy=false;
    $('downloadVectorSvg').disabled=false;
    $('downloadVectorPng').disabled=false;
  }
}
$('downloadVectorSvg').addEventListener('click',()=>{void exportTracedPicture('svg');});
$('downloadVectorPng').addEventListener('click',()=>{void exportTracedPicture('png');});

function revokeImageThumbnails() {
  for (const url of imageThumbUrls.values()) URL.revokeObjectURL(url);
  imageThumbUrls.clear();
  $('imagePreview').removeAttribute('src');
}

/* R3.2: integrated document text and movable figure blocks.
   This canvas represents document flow, not final pagination or float wrapping. */
function canvasImageStyle(item) {
  const pct=report?.text_width_cm ?
    Math.round(100 * item.inline_width_cm / report.text_width_cm) : 70;
  const original={width_percent:Math.max(25,Math.min(100,pct)),
                  align:'center',corner_percent:0};
  return Object.assign(original,imageEdits[String(item.index)]||{});
}
function styleFigure(index, patch) {
  const item=(report?.images||[]).find(x=>x.index===index);
  if (!item?.editable) return;
  const state={...canvasImageStyle(item),...patch};
  state.width_percent=Math.max(25,Math.min(100,Math.round(state.width_percent)));
  editWithUndo('picture-style:'+index,()=>{imageEdits[String(index)]=state;});
  selectedImage=index;
  renderDocumentCanvas();
  selectImage(index);
}
function moveFigureTo(index, beforeParagraph) {
  const item=(report?.images||[]).find(x=>x.index===index);
  const target=(report?.paragraphs||[]).find(x=>x.index===beforeParagraph);
  if (!item?.editable || !Number.isInteger(beforeParagraph) ||
      beforeParagraph<0 || beforeParagraph>report.paragraph_count) return;
  if (beforeParagraph!==report.paragraph_count &&
      (!target || !String(target.text||'').trim())) return;
  const targetSection=beforeParagraph===report.paragraph_count ?
    report.sections-1 : target?.section_index;
  if (item.section_index!==targetSection){
    $('canvasStatus').textContent='Нельзя переносить рисунок между разными разделами Word. '
      +'Размещение внутри своего раздела разрешено; границы разделов и колонтитулы защищены.';
    return;
  }
  if (beforeParagraph===item.paragraph_index ||
      beforeParagraph===item.caption_index ||
      (report.images||[]).some(x=>x.paragraph_index===beforeParagraph)) return;
  const captionIndices=new Set((report.images||[])
      .filter(x=>x.editable&&Number.isInteger(x.caption_index))
      .map(x=>x.caption_index));
  if (captionIndices.has(beforeParagraph)) return;
  editWithUndo('picture-move:'+index,()=>{
    imageMoveCounter=Math.min(1000000,imageMoveCounter+1);
    imageMoves[String(index)]={
      before_paragraph:beforeParagraph,order:imageMoveCounter
    };
  });
  selectedImage=index;
  renderDocumentCanvas();selectImage(index);
  $('canvasStatus').textContent='Рисунок №'+(index+1)+
    ' перенесён. Положение сохранится в DOCX/PDF после экспорта.';
}
function canvasDropZone(before) {
  const zone=document.createElement('button');
  zone.type='button';zone.className='canvasDrop';
  zone.dataset.beforeParagraph=String(before);
  zone.title='Поставить выбранный рисунок перед абзацем №'+(before+1);
  zone.setAttribute('aria-label',zone.title);
  zone.addEventListener('dragover',event=>{
    const formats=Array.from(event.dataTransfer?.types||[]);
    if (!formats.includes('application/x-achewiw-image')) return;
    event.preventDefault();event.dataTransfer.dropEffect='move';
    zone.classList.add('dragOver');
  });
  zone.addEventListener('dragleave',()=>zone.classList.remove('dragOver'));
  zone.addEventListener('drop',event=>{
    event.preventDefault();zone.classList.remove('dragOver');
    const raw=event.dataTransfer?.getData('application/x-achewiw-image');
    if (!/^\d+$/.test(raw||'')) return;
    moveFigureTo(Number(raw),before);
  });
  zone.addEventListener('click',()=>{
    if(selectedImage!==null) moveFigureTo(selectedImage,before);
  });
  zone.addEventListener('focus',()=>zone.classList.add('keyboardDrop'));
  zone.addEventListener('blur',()=>zone.classList.remove('keyboardDrop'));
  return zone;
}
function canvasFigure(item) {
  const key=String(item.index), style=canvasImageStyle(item);
  const fig=document.createElement('figure');
  fig.className='canvasFigure';
  fig.dataset.imageIndex=key;fig.draggable=true;
  if(selectedImage===item.index) fig.classList.add('selected');
  fig.title='Перетаскивай рисунок между абзацами вместе с подписью';
  fig.addEventListener('dragstart',event=>{
    event.dataTransfer.effectAllowed='move';
    event.dataTransfer.setData('application/x-achewiw-image',key);
    selectedImage=item.index;
  });
  fig.addEventListener('click',event=>{
    if(event.target.closest('button'))return;
    selectImage(item.index);
  });
  const tools=document.createElement('div');tools.className='figureTools';
  const title=document.createElement('span');title.textContent='⠿ Рисунок №'+(item.index+1)+' · перетащи за рамку';
  tools.append(title);
  const actions=[
    ['◀','Выровнять слева',()=>styleFigure(item.index,{align:'left'})],
    ['↔','По центру',()=>styleFigure(item.index,{align:'center'})],
    ['▶','Выровнять справа',()=>styleFigure(item.index,{align:'right'})],
    ['−','Уменьшить',()=>styleFigure(item.index,{
      width_percent:Math.max(25,style.width_percent-10)})],
    ['+','Увеличить',()=>styleFigure(item.index,{
      width_percent:Math.min(100,style.width_percent+10)})],
    ['◔','Скруглить',()=>styleFigure(item.index,{
      corner_percent:style.corner_percent===0?16:0})]
  ];
  for(const [label,titleText,handler] of actions) {
    const button=document.createElement('button');
    button.type='button';button.textContent=label;button.title=titleText;
    button.setAttribute('aria-label',titleText);
    button.addEventListener('click',handler);
    tools.append(button);
  }
  if(imageMoves[key]) {
    const revert=document.createElement('button');
    revert.type='button';revert.textContent='Вернуть место';
    revert.addEventListener('click',()=>{
      editWithUndo('picture-restore:'+key,()=>{delete imageMoves[key];});
      renderDocumentCanvas();
      $('canvasStatus').textContent='Рисунок вернулся на исходное место.';
    });
    tools.append(revert);
  }
  fig.append(tools);
  const line=document.createElement('div');line.className='canvasImageLine';
  line.style.justifyContent=
    style.align==='left'?'flex-start':style.align==='right'?'flex-end':'center';
  const img=document.createElement('img');
  img.className='canvasFigurePhoto';img.dataset.imageIndex=key;
  img.alt='Рисунок №'+(item.index+1);
  img.style.width=style.width_percent+'%';
  img.addEventListener('load',()=>cornerPixelRadius(img,style.corner_percent));
  const thumbnail=imageThumbUrls.get(item.index);
  if(thumbnail)img.src=thumbnail;
  line.append(img);fig.append(line);
  if (Number.isInteger(item.caption_index)) {
    const caption=report.paragraphs?.find(x=>x.index===item.caption_index);
    if(caption) {
      const label=document.createElement('figcaption');
      if (caption.editable) {
        const captionInput=document.createElement('textarea');
        captionInput.className='canvasCaptionText';
        captionInput.setAttribute('aria-label','Подпись к рисунку №'+(item.index+1));
        captionInput.rows=2;
        captionInput.spellcheck=false;
        captionInput.value=replacements[String(caption.index)]??caption.text;
        captionInput.addEventListener('focus',()=>selectTextParagraph(caption.index));
        captionInput.addEventListener('input',()=>{
          if(captionInput.value.length>5000)captionInput.value=captionInput.value.slice(0,5000);
          editWithUndo('type-caption:'+caption.index,()=>{
            if(captionInput.value===caption.text)delete replacements[String(caption.index)];
            else replacements[String(caption.index)]=captionInput.value;
          },true);
        });
        captionInput.addEventListener('dragstart',event=>event.stopPropagation());
        label.append(captionInput);
      } else {
        label.textContent=replacements[String(caption.index)]??caption.text;
      }
      fig.append(label);
    }
  }
  return fig;
}
function canvasText(row) {
  const content=replacements[String(row.index)]??row.text;
  if(!row.editable) {
    const p=document.createElement('p');
    p.className='canvasText';p.textContent=content;
    p.title='Сложный абзац: редактирование ограничено для защиты рисунков и полей';
    return p;
  }
  const area=document.createElement('textarea');
  area.className='canvasText';
  const kind=rowKind(row);
  if(kind==='heading1')area.classList.add('canvasHeading');
  if(kind==='heading2')area.classList.add('canvasSubheading');
  area.dataset.paragraphIndex=String(row.index);
  area.spellcheck=false;area.value=content;
  area.title='Нажми здесь и печатай — текст этого абзаца изменится в новой копии DOCX';
  area.dataset.align=paragraphAlignments[String(row.index)]||row.alignment||'inherit';
  area.rows=Math.max(2,Math.min(8,
    Math.ceil(content.length/110)+content.split('\n').length));
  area.setAttribute('aria-label','Абзац №'+(row.index+1)+' — редактирование на листе');
  area.addEventListener('focus',()=>selectTextParagraph(row.index));
  area.addEventListener('input',()=>{
    if(area.value.length>5000) {
      area.value=area.value.slice(0,5000);
      tell('Размер редактируемого абзаца ограничен 5000 символами',true);
    }
    editWithUndo('type-text:'+row.index,()=>{
      if(area.value===row.text) delete replacements[String(row.index)];
      else replacements[String(row.index)]=area.value;
      if(selected===row.index)$('paragraphText').value=area.value;
    },true);
  });
  return area;
}
function renderDocumentCanvas() {
  const canvas=$('documentCanvas');canvas.replaceChildren();
  $('documentEditor').hidden=report?.type!=='docx';
  $('runNormative').disabled=!draft||report?.type!=='docx';
  if(report?.type!=='docx')return;
  const captions=new Set((report.images||[])
    .filter(item=>item.editable&&Number.isInteger(item.caption_index))
    .map(item=>item.caption_index));
  const rows=(report.paragraphs||[])
    .filter(row=>!captions.has(row.index)&&String(row.text||'').trim())
    .sort((a,b)=>a.index-b.index);
  const images=(report.images||[])
    .filter(item=>item.editable&&Number.isInteger(item.paragraph_index));
  const anchors=new Map();
  for(const image of images){
    const move=imageMoves[String(image.index)];
    const next=rows.find(row=>row.index>image.paragraph_index);
    const anchor=move?.before_paragraph??(next?.index??report.paragraph_count);
    if(!anchors.has(anchor))anchors.set(anchor,[]);
    anchors.get(anchor).push(image);
  }
  for(const items of anchors.values()){
    items.sort((a,b)=>{
      const left=imageMoves[String(a.index)],right=imageMoves[String(b.index)];
      if(!!left!==!!right)return left?1:-1;
      return (left?.order??a.paragraph_index)-(right?.order??b.paragraph_index);
    });
  }
  const limit=Math.min(rows.length,canvasLimit);
  function appendBefore(position) {
    canvas.append(canvasDropZone(position));
    for(const item of anchors.get(position)||[])canvas.append(canvasFigure(item));
  }
  for(let i=0;i<limit;i++){
    const row=rows[i];
    appendBefore(row.index);
    canvas.append(canvasText(row));
  }
  const lastAnchor=limit<rows.length?rows[limit].index:report.paragraph_count;
  appendBefore(lastAnchor);
  const hiddenCount=Math.max(0,rows.length-limit);
  $('canvasMore').hidden=hiddenCount===0;
  $('canvasMore').textContent=hiddenCount?
    '↓ Показать ещё абзацы · '+hiddenCount+' скрыто':'';
  updateEditToolbar();
  $('canvasStatus').textContent='Щёлкни абзац и печатай. Выравнивание и отмена действий — в верхней панели. Рисунки перетаскивай '
    +'между абзацами (или выбери рисунок и нажми на полоску вставки). '
    +'Показано абзацев: '+limit+' из '+rows.length+'.';
}
$('canvasMore').addEventListener('click',()=>{
  canvasLimit=Math.min(3000,canvasLimit+120);
  renderDocumentCanvas();markDraftDirty();
});

function renderImageGallery() {
  revokeImageThumbnails();
  const list=$('imageList');
  list.replaceChildren();
  $('imageControls').hidden=true;
  const images=report?.images || [];
  if (report?.type==='pdf') {
    $('imageListStatus').textContent =
      'Для изображений исходного PDF сначала создайте редактируемый DOCX. '
      +'R1 сохраняет доступные растровые изображения, а этот редактор работает с DOCX.';
    return;
  }
  if (!images.length) {
    $('imageListStatus').textContent='В этом DOCX встроенные изображения не найдены.';
    return;
  }
  $('imageListStatus').textContent =
    'Найдено '+images.length+' изображений. '
    +'Применяются только выбранные изменения; сложные объекты остаются нетронутыми.';
  for (const item of images) {
    const button=document.createElement('button');
    button.type='button'; button.className='imageItem';
    button.disabled=!Number.isFinite(item.width_px)||!Number.isFinite(item.height_px);
    button.dataset.imageIndex=String(item.index);
    const thumb=document.createElement('img');
    thumb.alt='Иллюстрация №'+(item.index+1);
    thumb.loading='lazy';
    const title=document.createElement('strong');
    title.textContent='Рисунок №'+(item.index+1);
    const desc=document.createElement('small');
    desc.textContent=item.editable?
      item.width_px+'×'+item.height_px+
      (imageEdits[String(item.index)]?' · оформлен':'')+
      (imageAssets[String(item.index)]?' · заменён':''):
      (item.reason || 'Сложный объект');
    button.append(thumb,title,desc);
    button.addEventListener('click',()=>selectImage(item.index));
    list.append(button);
    if (draft) {
      const currentDraft=draft.id;
      const changedAsset=imageAssets[String(item.index)];
      const imagePath=changedAsset?
        '/api/docwork/drafts/'+currentDraft+'/assets/'+changedAsset:
        '/api/docwork/drafts/'+currentDraft+'/images/'+item.index;
      void fetch(imagePath,{
        cache:'no-store',
        headers:{'X-ACHEWIW-Docwork-Token':draft.token}
      }).then(response=>{
        if (!response.ok) throw Error('HTTP '+response.status);
        return response.blob();
      }).then(blob=>{
        if (!draft || draft.id!==currentDraft || !button.isConnected) return;
        const url=URL.createObjectURL(blob);
        imageThumbUrls.set(item.index,url);
        thumb.src=url;
        if (selectedImage===item.index) {
          $('imagePreview').src=url;
          $('imagePreview').onload=()=>cornerPixelRadius($('imagePreview'),
            $('imageCorners').value);
        }
        for(const pic of $('documentCanvas').querySelectorAll(
            '.canvasFigurePhoto[data-image-index="'+item.index+'"]')) pic.src=url;
      }).catch(()=>{
        desc.textContent='Миниатюра недоступна';
      });
    }
  }
}
function selectImage(index) {
  if (!report || !report.images) return;
  const item=report.images.find(x=>x.index===index);
  if (!item||!Number.isFinite(item.width_px)||!Number.isFinite(item.height_px)) return;
  if(selectedImage!==index){
    clearVectorPreview();
    $('vectorStatus').textContent='Экспериментальная трассировка не меняет оригинал.';
  }
  selectedImage=index;
  const saved=imageEdits[String(index)] || {
    width_percent:70, align:'center',corner_percent:0
  };
  $('imageWidth').value=String(saved.width_percent);
  $('imageAlign').value=saved.align;
  const cap=radiusLimit(item);
  $('imageCorners').max=String(cap);
  $('imageCornersNumber').max=String(cap);
  const safeRadius=controlledRadius(item,saved.corner_percent);
  $('imageCorners').value=String(safeRadius);
  $('imageCornersNumber').value=String(safeRadius);
  $('imageEditTitle').textContent='Рисунок №'+(index+1)+' · '+item.width_px+'×'+item.height_px;
  for(const id of ['imageWidth','imageAlign','imageCorners','imageCornersNumber',
    'applyImageEdit','resetImageEdit'])$(id).disabled=!item.editable;
  $('imageEditHint').textContent=item.editable ?
    'Редактируется только эта иллюстрация в новой копии DOCX/PDF. Текст и пропорции сохраняются.' :
    'Рисунок защищён: находится внутри таблицы или сложного элемента. '
      +'Изменять размещение и углы нельзя, но отдельная SVG/PNG-копия доступна для сравнения.';
  $('imageControls').hidden=false;
  $('imageList').querySelectorAll('.imageItem').forEach(button=>{
    button.classList.toggle('selected',Number(button.dataset.imageIndex)===index);
  });
  const url=imageThumbUrls.get(index);
  if (url) $('imagePreview').src=url;
  else $('imagePreview').removeAttribute('src');
  updateImageLivePreview();
  window.ACHEWIWWorkbench?.selectionChanged(index);
  for(const figure of $('documentCanvas').querySelectorAll('.canvasFigure')) {
    figure.classList.toggle('selected',
      Number(figure.dataset.imageIndex)===index);
  }
}
function updateImageLivePreview() {
  if (selectedImage===null) return;
  const width=Number($('imageWidth').value);
  const radius=Number($('imageCorners').value);
  const align=$('imageAlign').value;
  $('imageWidthLabel').textContent=width+'% ширины текста';
  const image=$('imagePreview');
  image.style.width=width+'%';
  image.dataset.imageIndex=String(selectedImage);
  const item=(report?.images||[]).find(x=>x.index===selectedImage);
  $('imageCornerHint').textContent=radius.toFixed(1)+'% · '
    +(item?.ratio>1.5?'широкая/высокая схема — мягкие углы':'пропорции соблюдены')
    +'; предел для этой картинки: '+radiusLimit(item)+'%';
  cornerPixelRadius(image,radius);
  $('imagePreview').parentNode.style.justifyContent=
      align==='left'?'flex-start':align==='right'?'flex-end':'center';
}
function tell(message, error=false) {
  const node = $('status'); node.textContent = message; node.classList.toggle('error', error);
  node.style.display = 'block'; clearTimeout(tell.timer);
  tell.timer = setTimeout(() => node.style.display = 'none', 5000);
}
function busy(flag) {
  for (const id of ['inspect','downloadDocx','downloadPdf','quickDownloadDocx','quickDownloadPdf','quickPreviewPdf','previewPdf'])
    $(id).disabled = flag || !source || (id !== 'inspect' && !report);
}
function clearPdf() {
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null; $('pdfFrame').removeAttribute('src');
  $('pdfPane').hidden = true; $('structurePane').hidden = false;
  $('showStructure').classList.add('selected');
  $('showStructure').setAttribute('aria-pressed','true');
}
function switchToPdf() {
  $('structurePane').hidden = true; $('pdfPane').hidden = false;
  $('imageWorkbench').hidden=true;
  $('openImageWorkbench').classList.remove('selected');
  $('openImageWorkbench').setAttribute('aria-pressed','false');
  $('showStructure').classList.remove('selected');
  $('showStructure').setAttribute('aria-pressed','false');
}
function reset() {
  resetNormativeReport();
  $('runNormative').disabled=true;
  clearPdf(); rendered = 80;
  $('paragraphSearch').value = '';
  $('paragraphFilter').value = 'headings';
  $('workflowStatus').textContent = 'Нажмите «Проверить структуру», затем выберите стиль.';
  report = null; selected = null;
  for (const key of Object.keys(overrides)) delete overrides[key];
  for (const key of Object.keys(replacements)) delete replacements[key];
  $('paragraphList').replaceChildren(); $('editPane').hidden = true;
  $('warnings').textContent = 'Проверьте документ, чтобы увидеть структуру.';
  $('summary').textContent = 'Содержимое остаётся только в выбранном файле.';
  $('pdfModeControls').hidden = true;
  $('pdfTextOnly').checked = false;
  $('pdfLossConfirmed').checked = false;
  selectedImage=null;canvasLimit=120;imageMoveCounter=0;
  clearVectorPreview();
  $('vectorStatus').textContent='Выбери рисунок для экспериментальной трассировки.';
  resetUndoTimeline();
  for (const key of Object.keys(imageEdits)) delete imageEdits[key];
  for (const key of Object.keys(imageMoves)) delete imageMoves[key];
  for (const key of Object.keys(imageAssets)) delete imageAssets[key];
  for (const key of Object.keys(paragraphAlignments)) delete paragraphAlignments[key];
  window.ACHEWIWWorkbench?.reset();
  revokeImageThumbnails();
  $('imageList').replaceChildren();
  $('imageControls').hidden=true;
  $('imageListStatus').textContent='После проверки DOCX здесь появятся рисунки.';
  $('documentCanvas').replaceChildren();
  $('documentEditor').hidden=true;
  $('changeCounter').textContent = '0 правок';
  updateEditToolbar();busy(false);
}
$('sourceFile').addEventListener('change', () => {
  if (draft) {
    $('sourceFile').value='';
    tell('Сначала сохраните и закройте текущий черновик, чтобы не потерять ключ восстановления.',true);
    return;
  }
  forgetDraft();
  source = $('sourceFile').files[0] || null; reset();
  $('fileLabel').textContent = source ? source.name + ' · ' + (source.size / 1048576).toFixed(2) + ' МиБ' : 'Файл не выбран';
  if (source) $('outputName').value = source.name.replace(/\.(docx|pdf)$/i, '') + ' — редакция';
});
function changeCount() {
  clearPdf();
  $('changeCounter').textContent =
    (new Set(Object.keys(overrides).concat(Object.keys(replacements),
       Object.keys(paragraphAlignments))).size
     + new Set(Object.keys(imageEdits).concat(Object.keys(imageMoves),Object.keys(imageAssets))).size)
       + ' ручных правок';
}
function rowKind(row) {
  return overrides[row.index] && overrides[row.index] !== 'keep' ? overrides[row.index] : row.kind;
}
function drawRows() {
  const root = $('paragraphList'); root.replaceChildren();
  if (!report) return;
  if (report.type === 'pdf') {
    for (const item of report.sample_pages || []) {
      const panel = document.createElement('article'); panel.className = 'paragraphRow';
      const body = document.createElement('div'); body.className = 'body';
      const heading = document.createElement('strong'); heading.textContent = 'Страница ' + item.page;
      const para = document.createElement('p'); para.textContent = item.text || '(текст не найден)';
      para.style.whiteSpace = 'pre-wrap'; body.append(heading, para); panel.append(body); root.append(panel);
    }
    $('outlineCount').textContent = 'Первые страницы PDF — текстовый слой';
    $('loadMoreParagraphs').hidden = true;
    return;
  }
  const query = $('paragraphSearch').value.trim().toLocaleLowerCase('ru');
  const headingsOnly = $('paragraphFilter').value === 'headings';
  const rows = (report.paragraphs || []).filter(item => {
    const name = (item.style || '').toLowerCase();
    const heading = rowKind(item) !== 'body' || /^heading\s|^title$|^subtitle$|^заголовок\s/.test(name);
    if (headingsOnly && !heading) return false;
    return !query || String(Object.prototype.hasOwnProperty.call(replacements,item.index) ?
      replacements[item.index] : item.text).toLocaleLowerCase('ru').includes(query);
  });
  $('outlineCount').textContent = 'Найдено: ' + rows.length + ' · показано: ' +
    Math.min(rendered, rows.length) + ' из ' + report.paragraph_count + ' абзацев книги';
  $('loadMoreParagraphs').hidden = rows.length <= rendered;
  for (const item of rows.slice(0,rendered)) {
    const key = String(item.index), button = document.createElement('button');
    button.type = 'button'; button.className = 'paragraphRow'; button.dataset.role = rowKind(item);
    if (selected === item.index) button.classList.add('selected');
    const number = document.createElement('span'); number.className = 'index'; number.textContent = '#' + (item.index+1);
    const body = document.createElement('span'); body.className = 'body';
    const text = document.createElement('strong');
    const fullText = Object.prototype.hasOwnProperty.call(replacements,key) ? replacements[key] : item.text;
    text.textContent = fullText.length > 320 ? fullText.slice(0, 320) + '…' : fullText;
    const meta = document.createElement('small'), labels = {heading1:'Глава',heading2:'Подглава',body:'Текст'};
    meta.textContent = (labels[rowKind(item)] || 'Абзац') + ' · ' + item.style +
      ((overrides[key] || Object.prototype.hasOwnProperty.call(replacements,key)) ? ' · изменено' : '') +
      (!item.editable ? ' · сложный абзац' : '');
    body.append(text, meta); button.append(number, body);
    button.addEventListener('click', () => editParagraph(item.index)); root.append(button);
  }
}
function editParagraph(index) {
  const row = report && report.paragraphs && report.paragraphs.find(x => x.index === index);
  if (!row) return;
  selected = index; $('editPane').hidden = false;
  $('editTitle').textContent = 'Редактирование абзаца #' + (index+1);
  $('paragraphRole').value = overrides[index] || 'keep';
  $('paragraphText').value = Object.prototype.hasOwnProperty.call(replacements,index) ? replacements[index] : row.text;
  $('paragraphText').disabled = !row.editable;
  $('editWarning').textContent = row.editable ?
    'Текст изменится только в этом абзаце. Локальное форматирование внутри изменённой строки может упроститься; остальные элементы не меняются.' :
    'Сложный абзац: рисунки, поля или гиперссылки. Можно поменять роль заголовка, но нельзя переписать текст.';
  drawRows();
  markDraftDirty();
}
$('saveParagraph').addEventListener('click', () => {
  const item = report && report.paragraphs && report.paragraphs.find(x => x.index === selected);
  if (!item) return;
  const key = String(item.index), role = $('paragraphRole').value;
  editWithUndo('advanced-paragraph:'+item.index,()=>{
    if (role === 'keep') delete overrides[key]; else overrides[key] = role;
    if (item.editable) {
      const edited = $('paragraphText').value;
      if (edited !== item.text) replacements[key] = edited;
      else delete replacements[key];
    }
  });
  drawRows();renderDocumentCanvas();
  tell('Правка подготовлена и будет сохранена. Для новой редакции документа нажмите «Скачать».');
});
$('resetParagraph').addEventListener('click', () => {
  if (selected === null) return;
  editWithUndo('reset-paragraph:'+selected,()=>{
    delete overrides[selected]; delete replacements[selected];
    delete paragraphAlignments[selected];
  });
  editParagraph(selected); renderDocumentCanvas();
  tell('Локальная правка сброшена.');
});
async function inspect(restoring=false) {
  if (!source) return;
  busy(true); $('warnings').textContent = 'Проверяем структуру файла…';
  try {
    const data = new FormData(); data.append('file', source, source.name);
    const reusing=restoring||!!draft;
    const response = await fetch(reusing?'/api/docwork/inspect':'/api/docwork/drafts',
      {method:'POST',body:data,cache:'no-store'});
    const mime = response.headers.get('content-type') || '';
    if (!mime.includes('application/json'))
      throw new Error('Сервер вернул не JSON (HTTP '+response.status+'). Обновите страницу и повторите.');
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'HTTP '+response.status);
    if (!reusing) {
      draft={id:payload.id,token:payload.token,revision:payload.revision};
      lastSavedState='{}';
      rememberDraft();draftConflicted=false; draftControls();
      draftMessage('Исходный файл сохранён на сервере · редакция №1');
      void refreshDraftHistory();
    }
    const result=reusing?payload:payload.preview;
    report = result; selected = null; rendered = 80; $('editPane').hidden = true;
    $('pdfModeControls').hidden = result.type !== 'pdf';
    clearPdf();
    $('workflowStatus').textContent = 'Файл прочитан · выбери стиль, затем нажми «Показать страницы PDF».';
    $('paragraphFilter').value = result.type === 'docx' ? 'headings' : 'all';
    $('summary').textContent = result.type === 'docx' ?
      'Абзацев: '+result.paragraph_count+'; таблиц: '+result.tables+'; разделов: '+result.sections :
      result.pages+' стр. · символов: '+result.characters;
    $('warnings').textContent = (result.warnings || []).join(' ');
    if (result.type === 'pdf') $('warnings').textContent += ' Отдельные абзацы станут редактируемыми после преобразования в DOCX.';
    drawRows();renderImageGallery();renderDocumentCanvas();
    window.ACHEWIWWorkbench?.refresh();
    tell('Документ прочитан. Можно менять стиль и скачивать новую редакцию.');
    if (!reusing) markDraftDirty();
  } catch (e) {
    report = null; $('warnings').textContent = 'Ошибка проверки: '+e.message; tell(e.message, true);
  } finally {busy(false);}
}
$('inspect').addEventListener('click', () => { void inspect(false); });
function optionsForExport() {
  return {preset:$('preset').value,output_name:$('outputName').value,
    auto_headings:$('autoHeadings').checked, clear_highlights:$('clearHighlights').checked,
    hide_proofing:$('hideProofing').checked, heading_bold:$('headingBold').checked,
    body_font:$('bodyFont').value, chapter_font:$('chapterFont').value,
    subchapter_font:$('subchapterFont').value, body_size:$('bodySize').value,
    chapter_size:$('chapterSize').value, subchapter_size:$('subchapterSize').value,
    pdf_mode:$('pdfTextOnly').checked ? 'text_only' : 'preserve',
    confirm_text_loss:$('pdfLossConfirmed').checked,
    overrides, replacements,
    paragraph_alignments:{...paragraphAlignments},
    image_edits:{...imageEdits}, image_moves:{...imageMoves},
    image_assets:{...imageAssets}};
}
function requestForm(target, options) {
  const form = new FormData(); form.append('file', source, source.name);
  form.append('target', target); form.append('options', JSON.stringify(options));
  if (draft) form.append('draft_id',draft.id);
  return form;
}
function authenticatedExportHeaders() {
  return draft && Object.keys(imageAssets).length>0 ?
    {'X-ACHEWIW-Docwork-Token':draft.token} : {};
}

async function download(target) {
  if (!source || !report) return;
  busy(true);
  try {
    const options = optionsForExport();
    const form = requestForm(target,options);
    tell('Готовим новый файл…');
    const response = await fetch('/api/docwork/export', {
      method:'POST',body:form,cache:'no-store',headers:authenticatedExportHeaders()
    });
    if (!response.ok) {
      let error = 'HTTP '+response.status;
      try {error = (await response.json()).detail || error;} catch {}
      throw new Error(error);
    }
    const disp = response.headers.get('Content-Disposition') || '';
    const encoded = disp.match(/filename\*=UTF-8''([^;]+)/i);
    const name = encoded ? decodeURIComponent(encoded[1]) : (options.output_name || 'ACHEWIW_document')+'.'+target;
    const blob = await response.blob(), url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = name;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    $('workflowStatus').textContent = 'Файл успешно создан: '+name;
    tell('Файл создан: '+name);
  } catch (e) {tell('Конвертация не удалась: '+e.message, true);}
  finally {busy(false);}
}
async function buildPdfPreview() {
  if (!source || !report) return;
  busy(true);
  $('workflowStatus').textContent = 'Формируем страницы PDF из настроек книги…';
  try {
    let blob;
    if (report.type === 'pdf') blob = source;
    else {
      const response = await fetch('/api/docwork/export', {
        method:'POST',body:requestForm('pdf',optionsForExport()),cache:'no-store',
        headers:authenticatedExportHeaders()
      });
      if (!response.ok) {
        let detail = 'HTTP '+response.status;
        try {detail = (await response.json()).detail || detail;} catch {}
        throw new Error(detail);
      }
      blob = await response.blob();
      if (!blob.type.includes('pdf')) throw new Error('Конвертер вернул не PDF');
    }
    clearPdf(); previewUrl = URL.createObjectURL(blob);
    $('pdfFrame').src = previewUrl;
    $('pdfPreviewStatus').textContent = report.type === 'pdf' ?
      'Просмотр исходного PDF. Для изменения оформления сначала скачайте DOCX.' :
      'Реальный результат DOCX → PDF с текущими настройками. Сверьте страницы, затем скачайте копию.';
    switchToPdf();
    $('workflowStatus').textContent = 'PDF-предпросмотр готов. Проверьте страницы и скачайте файл.';
    $('pdfPane').scrollIntoView({behavior:'smooth',block:'start'});
  } catch (e) {
    $('workflowStatus').textContent = 'Ошибка PDF-предпросмотра: '+e.message;
    tell('Не удалось показать PDF: '+e.message,true);
  } finally {busy(false);}
}
$('paragraphSearch').addEventListener('input',()=>{rendered=80;drawRows();markDraftDirty();});
$('paragraphFilter').addEventListener('change',()=>{rendered=80;drawRows();markDraftDirty();});
$('loadMoreParagraphs').addEventListener('click',()=>{rendered+=80;drawRows();markDraftDirty();});
$('showStructure').addEventListener('click',()=>{
  $('pdfPane').hidden=true;$('structurePane').hidden=false;
  $('imageWorkbench').hidden=true;
  $('openImageWorkbench').classList.remove('selected');
  $('openImageWorkbench').setAttribute('aria-pressed','false');
  $('showStructure').classList.add('selected');
  $('showStructure').setAttribute('aria-pressed','true');
});
for (const id of ['preset','bodyFont','chapterFont','subchapterFont','bodySize','chapterSize',
                  'subchapterSize','headingBold','autoHeadings','clearHighlights','hideProofing']) {
  $(id).addEventListener('change',()=>{
    if (previewUrl) {
      clearPdf();
      $('workflowStatus').textContent = 'Настройки изменены. Постройте PDF-предпросмотр заново.';
    }
    markDraftDirty();
  });
}
$('pdfTextOnly').addEventListener('change', () => {
  if (!$('pdfTextOnly').checked) $('pdfLossConfirmed').checked = false;
  clearPdf();markDraftDirty();
});
$('downloadDocx').addEventListener('click', () => download('docx'));
$('downloadPdf').addEventListener('click', () => download('pdf'));
$('quickDownloadDocx').addEventListener('click', () => download('docx'));
$('quickDownloadPdf').addEventListener('click', () => download('pdf'));
$('previewPdf').addEventListener('click', buildPdfPreview);
$('quickPreviewPdf').addEventListener('click', buildPdfPreview);
for(const id of ['imageWidth','imageAlign']){
  $(id).addEventListener('input',updateImageLivePreview);
  $(id).addEventListener('change',updateImageLivePreview);
}
$('imageCorners').addEventListener('input',()=>{
  $('imageCornersNumber').value=$('imageCorners').value;
  updateImageLivePreview();
});
$('imageCornersNumber').addEventListener('input',()=>{
  const item=(report?.images||[]).find(x=>x.index===selectedImage);
  const pct=controlledRadius(item,$('imageCornersNumber').value);
  $('imageCorners').value=String(pct);
  $('imageCornersNumber').value=String(pct);
  updateImageLivePreview();
});
$('applyImageEdit').addEventListener('click',()=>{
  if (selectedImage===null) return;
  editWithUndo('picture-style:'+selectedImage,()=>{
    imageEdits[String(selectedImage)]={
      width_percent:Number($('imageWidth').value),
      align:$('imageAlign').value,
      corner_percent:Number($('imageCorners').value)
    };
  });
  renderDocumentCanvas();
  const item=$('imageList').querySelector('[data-image-index="'+selectedImage+'"] small');
  if (item) item.textContent='Оформлен · '+$('imageWidth').value+'% · '+$('imageAlign').value;
  tell('Рисунок №'+(selectedImage+1)+' будет оформлен в новой копии документа.');
});
$('resetImageEdit').addEventListener('click',()=>{
  if (selectedImage===null) return;
  const index=selectedImage;
  editWithUndo('picture-reset:'+index,()=>{delete imageEdits[String(index)];});
  selectImage(index);
  renderDocumentCanvas();
  tell('Оформление рисунка сброшено к исходному.');
});
$('undoEdit').addEventListener('click',()=>undoEditAction(false));
$('redoEdit').addEventListener('click',()=>undoEditAction(true));
for(const id of ['alignLeft','alignCenter','alignRight','alignJustify','alignReset']) {
  $(id).addEventListener('click',()=>{
    const index=activeParagraph,row=report?.paragraphs?.find(x=>x.index===index);
    if(!row?.editable||report?.type!=='docx')return;
    const alignment=$(id).dataset.align;
    editWithUndo('paragraph-align:'+index,()=>{
      if(alignment==='inherit')delete paragraphAlignments[String(index)];
      else paragraphAlignments[String(index)]=alignment;
    });
    renderDocumentCanvas();
    updateEditToolbar();
    const editor=$('documentCanvas').querySelector(
      '.canvasText[data-paragraph-index="'+index+'"]');
    editor?.focus({preventScroll:true});
  });
}
$('canvasRole').addEventListener('change',()=>{
  const index=activeParagraph,row=report?.paragraphs?.find(x=>x.index===index);
  if(!row?.editable||report?.type!=='docx')return;
  const role=$('canvasRole').value;
  editWithUndo('paragraph-role:'+index,()=>{
    if(role==='keep')delete overrides[String(index)];
    else overrides[String(index)]=role;
  });
  drawRows();renderDocumentCanvas();updateEditToolbar();
  $('documentCanvas').querySelector(
    '.canvasText[data-paragraph-index="'+index+'"]')?.focus({preventScroll:true});
});
document.addEventListener('keydown',event=>{
  if(!(event.ctrlKey||event.metaKey)||event.altKey||!report||report.type!=='docx')return;
  // Do not hijack native undo in filename, search, other form controls.
  if(!event.target.closest('#documentEditor'))return;
  const key=event.key.toLowerCase();
  if(key==='z'||key==='y'){
    const redo=key==='y'||(key==='z'&&event.shiftKey);
    if((redo?redoStack:undoStack).length===0)return;
    event.preventDefault();
    undoEditAction(redo);
  }
});
$('outputName').addEventListener('input',markDraftDirty);
$('pdfLossConfirmed').addEventListener('change',markDraftDirty);
$('paragraphText').addEventListener('input',markDraftDirty);
$('paragraphRole').addEventListener('change',markDraftDirty);
async function manualSave() {
  if(!draft)return;
  await saveDraftNow();
  await refreshDraftHistory();
}
for(const id of ['saveDraft','quickSaveDraft','saveCanvasNow'])
  $(id).addEventListener('click',()=>{void manualSave();});
$('draftHistory').addEventListener('change',draftControls);
$('restoreDraftVersion').addEventListener('click',async()=>{
  const version=Number($('draftHistory').value);
  if (!draft||!version||!confirm('Восстановить редакцию №'+version+
      '? Будет создана новая редакция, сохранённые версии останутся без изменений.')) return;
  try {
    const data=await (await draftFetch('/history/'+version)).json();
    applyDraftState(data.state);markDraftDirty();await saveDraftNow();
    await refreshDraftHistory();
  } catch(e){draftMessage('Не удалось восстановить редакцию: '+e.message,true);}
});
$('reloadDraft').addEventListener('click',async()=>{
  if (!draft||!confirm('Перезагрузить последнюю версию с сервера? Сначала скачайте несохранённые правки JSON.')) return;
  try {
    const data=await (await draftFetch('')).json();
    draft.revision=data.revision;draftConflicted=false;recoveredLocalScratch=null;rememberDraft();
    lastSavedState=canonicalState(data.state);
    clearTimeout(draftSaveTimer);applyDraftState(data.state);scratchDraft();
    draftMessage('Загружена серверная редакция №'+data.revision);
    await refreshDraftHistory();
  } catch(e) { draftMessage('Не удалось обновить редакцию: '+e.message,true); }
});
$('exportDraftState').addEventListener('click',()=>{
  if (!draft) return;
  const state=recoveredLocalScratch?.id===draft.id ?
    recoveredLocalScratch.state : captureDraftState();
  const baseRevision=recoveredLocalScratch?.id===draft.id ?
    recoveredLocalScratch.revision : draft.revision;
  const file=new Blob([JSON.stringify({id:draft.id,revision:baseRevision,state},null,2)],
    {type:'application/json'});
  const url=URL.createObjectURL(file);
  const link=document.createElement('a');link.href=url;link.download='ACHEWIW-docwork-my-edits.json';
  document.body.append(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),60000);
});
$('closeDraft').addEventListener('click',async()=>{
  if (!draft||!confirm('Удалить ТОЛЬКО этот временный черновик DocWork и его историю? Перед этим скачайте готовый документ. Книги в библиотеке ACHEWIW не затрагиваются.')) return;
  try {
    const response=await fetch('/api/docwork/drafts/'+draft.id,{
      method:'DELETE',cache:'no-store',headers:{
        'X-ACHEWIW-Docwork-Token':draft.token,
        'X-ACHEWIW-Confirm-Close-Draft':'yes-delete-only-this-draft'
      }});
    if (!response.ok) throw new Error('HTTP '+response.status);
    forgetDraft();draft=null;draftConflicted=false;lastSavedState='{}';
    recoveredLocalScratch=null;source=null;
    $('sourceFile').value='';reset();draftControls();
    $('fileLabel').textContent='Файл не выбран';
    draftMessage('Черновик закрыт по подтверждению пользователя. Можно загрузить новый файл.');
  } catch(e) { draftMessage('Не удалось закрыть черновик: '+e.message,true); }
});
window.addEventListener('pagehide',()=>{
  if (draft&&report) {
    scratchDraft();
    if (!draftSaveBusy&&!draftConflicted &&
        canonicalState(captureDraftState())!==lastSavedState) {
      // Best effort only. sessionStorage scratch protects unsent edits on F5.
      const state=captureDraftState();
      void fetch('/api/docwork/drafts/'+draft.id,{
        method:'PUT',keepalive:true,
        headers:{'Content-Type':'application/json','X-ACHEWIW-Docwork-Token':draft.token},
        body:JSON.stringify({revision:draft.revision,state})
      }).catch(()=>{});
    }
  }
});
window.addEventListener('beforeunload',()=>{if(previewUrl)URL.revokeObjectURL(previewUrl);});
/* R4.0 — audit a fresh exported DOCX without changing the manuscript. */
let lastNormativeReport=null;
function resetNormativeReport(){
  lastNormativeReport=null;
  $('downloadNormative').disabled=true;
  $('normativeReport').replaceChildren();
  $('normativeStatus').textContent='Проверка не выполнена. Выберите профиль и нажмите «Проверить DOCX».';
}
function addNormativeRow(root,label,value){
  const line=document.createElement('div');
  line.className='normativeRow';
  const left=document.createElement('strong');
  left.textContent=label;
  const right=document.createElement('span');
  right.textContent=String(value);
  line.append(left,right);root.append(line);
}
function displayNormativeReport(data){
  const root=$('normativeReport');
  root.replaceChildren();
  const title=document.createElement('h3');
  title.textContent='Диагностика · '+data.profile_title;
  root.append(title);
  const note=document.createElement('p');
  note.className='hint';note.textContent=data.scope_note;root.append(note);
  const table=document.createElement('div');
  table.className='normativeRows';
  const items=[
    ['Абзацы',data.metrics.paragraphs],['Разделы',data.metrics.sections],
    ['Таблицы',data.metrics.tables],['Рисунки',data.metrics.pictures],
    ['Подписи (возможные)',data.metrics.captions_candidates],
    ['Сноски',data.metrics.footnotes],['Концевые примечания',data.metrics.endnotes],
    ['Поля оглавления',data.metrics.toc_field_commands],
    ['Перекрёстные ссылки',data.metrics.cross_reference_field_commands],
    ['Список литературы',data.metrics.bibliography_heading_found?'Заголовок найден':'Не обнаружен'],
    ['Позиции библиографии (примерно)',data.metrics.bibliography_entry_candidates]
  ];
  for(const [name,value] of items)addNormativeRow(table,name,value);
  root.append(table);
  const scopes=document.createElement('p');scopes.className='hint';
  scopes.textContent=data.standards.length?
    'Справочные стандарты: '+data.standards.map(item=>item.number).join(', '):
    'Нормативный профиль не выбран';
  root.append(scopes);
  for(const item of data.page_geometry.slice(0,25)){
    const p=document.createElement('p');p.className='hint';
    p.textContent='Раздел '+item.section+': '+item.width_cm+'×'+item.height_cm+
      ' см; поля В/Н/Л/П: '+item.top_cm+'/'+item.bottom_cm+'/'+
      item.left_cm+'/'+item.right_cm+' см';
    root.append(p);
  }
  for(const item of data.observations){
    const row=document.createElement('p');row.className='normativeObservation';
    row.textContent=(item.level==='review'?'Проверить · ':'Сведения · ')+item.message;
    root.append(row);
  }
  const footer=document.createElement('p');footer.className='hint';
  footer.textContent='Отчёт по отдельной DOCX-копии SHA-256: '+data.source_sha256+
    '. Это не сертификат ГОСТ и не разрешение публиковать книгу.';
  root.append(footer);
}
async function runNormativeAudit(){
  if(!source||!draft||report?.type!=='docx')return;
  $('runNormative').disabled=true;
  resetNormativeReport();
  $('normativeStatus').textContent='Готовим текущую DOCX-редакцию для проверки…';
  const atStart=JSON.stringify(optionsForExport());
  try{
    const exportedResponse=await fetch('/api/docwork/export',{
      method:'POST',body:requestForm('docx',optionsForExport()),
      headers:authenticatedExportHeaders(),cache:'no-store'
    });
    if(!exportedResponse.ok){
      let msg='HTTP '+exportedResponse.status;
      try{msg=(await exportedResponse.json()).detail||msg;}catch{}
      throw new Error('Сборка DOCX: '+msg);
    }
    const copy=await exportedResponse.blob();
    const form=new FormData();
    form.append('file',copy,'ACHEWIW-current-edition.docx');
    form.append('profile',$('normativeProfile').value);
    const response=await fetch('/api/docwork/preflight',{
      method:'POST',body:form,cache:'no-store'
    });
    if(!response.ok){
      let msg='HTTP '+response.status;
      try{msg=(await response.json()).detail||msg;}catch{}
      throw new Error(msg);
    }
    const result=await response.json();
    if(atStart!==JSON.stringify(optionsForExport())){
      $('normativeStatus').textContent='Настройки изменились во время проверки. Запустите её ещё раз.';
      return;
    }
    lastNormativeReport=result;
    $('downloadNormative').disabled=false;
    displayNormativeReport(result);
    $('normativeStatus').textContent='Отчёт готов. Нужна ручная полиграфическая и нормативная проверка.';
  }catch(error){
    $('normativeStatus').textContent='Проверка не выполнена: '+error.message;
  }finally{
    $('runNormative').disabled=!draft||report?.type!=='docx';
  }
}
$('runNormative').addEventListener('click',()=>{void runNormativeAudit();});
$('normativeProfile').addEventListener('change',resetNormativeReport);
$('downloadNormative').addEventListener('click',()=>{
  if(!lastNormativeReport)return;
  const blob=new Blob([JSON.stringify(lastNormativeReport,null,2)],{
    type:'application/json;charset=utf-8'
  });
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;
  a.download='ACHEWIW-R4-preflight-'+lastNormativeReport.profile+'.json';
  document.body.append(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(url),60000);
});

window.ACHEWIWWorkbench?.connect({
  current:()=>({draft,report,selected:selectedImage}),
  selectPicture:index=>selectImage(index),
  assetFor:index=>imageAssets[String(index)]||null,
  applyAsset:(index,id)=>{
    editWithUndo('replace-artwork:'+index,()=>{imageAssets[String(index)]=id;});
    renderDocumentCanvas();renderImageGallery();
    window.ACHEWIWWorkbench?.refresh();
  },
  restoreAsset:index=>{
    editWithUndo('restore-artwork:'+index,()=>{delete imageAssets[String(index)];});
    renderDocumentCanvas();renderImageGallery();
    window.ACHEWIWWorkbench?.refresh();
  }
});
draftControls();
draftMessage('Выбранный документ ещё не сохранён на сервере.');
void restoreDraft();
})();
