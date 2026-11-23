(() => {
'use strict';
const $ = id => document.getElementById(id);
let source = null, report = null, selected = null, previewUrl = null, rendered = 80, processing = false;
const overrides = Object.create(null), replacements = Object.create(null);

let draft = null, draftSaveTimer = null, draftSaveBusy = false, draftConflicted = false;
const DRAFT_KEY = 'achewiw-docwork-r3-current';
const SCRATCH_KEY = 'achewiw-docwork-r3-pending';
const settingsIds = {
  preset:'preset', output_name:'outputName', auto_headings:'autoHeadings',
  clear_highlights:'clearHighlights', hide_proofing:'hideProofing', heading_bold:'headingBold',
  body_font:'bodyFont', chapter_font:'chapterFont', subchapter_font:'subchapterFont',
  body_size:'bodySize', chapter_size:'chapterSize', subchapter_size:'subchapterSize'
};
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
  return {
    settings, overrides:{...overrides}, replacements:{...replacements},
    editor:{selected, text: selected === null ? '' : $('paragraphText').value,
            role: selected === null ? 'keep' : $('paragraphRole').value},
    ui:{search:$('paragraphSearch').value, filter:$('paragraphFilter').value, rendered}
  };
}
function scratchDraft() {
  if (!draft || !report) return;
  try { sessionStorage.setItem(SCRATCH_KEY, JSON.stringify({
    id:draft.id, revision:draft.revision, state:captureDraftState()
  })); } catch { $('draftStatus').textContent = 'Локальная страховочная копия не поместилась в браузер. Нажмите «Сохранить правки сейчас».'; }
}
function draftControls() {
  for (const id of ['saveDraft','reloadDraft','exportDraftState','closeDraft'])
    $(id).disabled = !draft;
  $('restoreDraftVersion').disabled = !draft || !$('draftHistory').value;
  $('draftHistory').disabled = !draft;
}
function draftMessage(text, error=false) {
  $('draftStatus').textContent = text;
  $('draftStatus').classList.toggle('error',error);
}
function applyDraftState(state) {
  if (!state || !report) return;
  for (const obj of [overrides,replacements])
    for (const key of Object.keys(obj)) delete obj[key];
  Object.assign(overrides,state.overrides || {});
  Object.assign(replacements,state.replacements || {});
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
    new Set(Object.keys(overrides).concat(Object.keys(replacements))).size+' ручных правок';
  selected=null; $('editPane').hidden=true; drawRows();
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
  scratchDraft();
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
    $('fileLabel').textContent=meta.filename+' · восстановлено с сервера';
    await inspect(true);
    if (!report) throw new Error('Не удалось заново прочитать структуру документа');
    applyDraftState(meta.state);
    let pending=null;
    try { pending=JSON.parse(sessionStorage.getItem(SCRATCH_KEY)||'null'); } catch {}
    if (pending?.id===draft.id && pending.revision===draft.revision) {
      if (JSON.stringify(pending.state)!==JSON.stringify(meta.state)) {
        applyDraftState(pending.state); markDraftDirty();
        draftMessage('Восстановлены изменения перед F5 · синхронизация с сервером…');
      }
    } else if (pending?.id===draft.id && pending.revision < draft.revision) {
      draftMessage('Сервер содержит более новую редакцию. При необходимости сохраните локальные правки JSON перед продолжением.',true);
    }
    await refreshDraftHistory();
    if (!draftSaveTimer) draftMessage('Редактор восстановлен · редакция №'+draft.revision);
  } catch(e) {
    draftMessage('Восстановление не удалось: '+e.message+
      '. Ключ черновика сохранён в этой вкладке, исходный файл не удалён.',true);
  }
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
  $('showStructure').classList.remove('selected');
  $('showStructure').setAttribute('aria-pressed','false');
}
function reset() {
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
  $('changeCounter').textContent = '0 правок'; busy(false);
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
  $('changeCounter').textContent = new Set(Object.keys(overrides).concat(Object.keys(replacements))).size + ' ручных правок';
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
  if (role === 'keep') delete overrides[key]; else overrides[key] = role;
  if (item.editable) {
    const edited = $('paragraphText').value;
    if (edited !== item.text) replacements[key] = edited;
    else delete replacements[key];
  }
  changeCount(); drawRows(); markDraftDirty();
  tell('Правка подготовлена и будет сохранена. Для новой редакции документа нажмите «Скачать».');
});
$('resetParagraph').addEventListener('click', () => {
  if (selected === null) return;
  delete overrides[selected]; delete replacements[selected];
  changeCount(); editParagraph(selected); markDraftDirty(); tell('Локальная правка сброшена.');
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
    drawRows(); tell('Документ прочитан. Можно менять стиль и скачивать новую редакцию.');
  } catch (e) {
    report = null; $('warnings').textContent = 'Ошибка проверки: '+e.message; tell(e.message, true);
  } finally {busy(false);}
}
$('inspect').addEventListener('click', inspect);
function optionsForExport() {
  return {preset:$('preset').value,output_name:$('outputName').value,
    auto_headings:$('autoHeadings').checked, clear_highlights:$('clearHighlights').checked,
    hide_proofing:$('hideProofing').checked, heading_bold:$('headingBold').checked,
    body_font:$('bodyFont').value, chapter_font:$('chapterFont').value,
    subchapter_font:$('subchapterFont').value, body_size:$('bodySize').value,
    chapter_size:$('chapterSize').value, subchapter_size:$('subchapterSize').value,
    pdf_mode:$('pdfTextOnly').checked ? 'text_only' : 'preserve',
    confirm_text_loss:$('pdfLossConfirmed').checked,
    overrides, replacements};
}
function requestForm(target, options) {
  const form = new FormData(); form.append('file', source, source.name);
  form.append('target', target); form.append('options', JSON.stringify(options));
  return form;
}

async function download(target) {
  if (!source || !report) return;
  busy(true);
  try {
    const options = optionsForExport();
    const form = requestForm(target,options);
    tell('Готовим новый файл…');
    const response = await fetch('/api/docwork/export', {method:'POST',body:form,cache:'no-store'});
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
        method:'POST',body:requestForm('pdf',optionsForExport()),cache:'no-store'
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
$('outputName').addEventListener('input',markDraftDirty);
$('pdfLossConfirmed').addEventListener('change',markDraftDirty);
$('paragraphText').addEventListener('input',markDraftDirty);
$('paragraphRole').addEventListener('change',markDraftDirty);
$('saveDraft').addEventListener('click',async()=>{
  await saveDraftNow();await refreshDraftHistory();
});
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
    draft.revision=data.revision;draftConflicted=false;rememberDraft();
    clearTimeout(draftSaveTimer);applyDraftState(data.state);scratchDraft();
    draftMessage('Загружена серверная редакция №'+data.revision);
    await refreshDraftHistory();
  } catch(e) { draftMessage('Не удалось обновить редакцию: '+e.message,true); }
});
$('exportDraftState').addEventListener('click',()=>{
  if (!draft) return;
  const state=captureDraftState();
  const file=new Blob([JSON.stringify({id:draft.id,revision:draft.revision,state},null,2)],
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
    forgetDraft();draft=null;draftConflicted=false;source=null;
    $('sourceFile').value='';reset();draftControls();
    $('fileLabel').textContent='Файл не выбран';
    draftMessage('Черновик закрыт по подтверждению пользователя. Можно загрузить новый файл.');
  } catch(e) { draftMessage('Не удалось закрыть черновик: '+e.message,true); }
});
window.addEventListener('pagehide',()=>{
  if (draft&&report) {
    scratchDraft();
    if (!draftSaveBusy&&!draftConflicted) {
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
draftControls();
void restoreDraft();
})();
