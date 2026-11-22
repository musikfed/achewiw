(() => {
'use strict';
const $ = id => document.getElementById(id);
let source = null, report = null, selected = null;
const overrides = Object.create(null), replacements = Object.create(null);
function tell(message, error=false) {
  const node = $('status'); node.textContent = message; node.classList.toggle('error', error);
  node.style.display = 'block'; clearTimeout(tell.timer);
  tell.timer = setTimeout(() => node.style.display = 'none', 5000);
}
function busy(flag) {
  for (const id of ['inspect','downloadDocx','downloadPdf'])
    $(id).disabled = flag || !source || (id !== 'inspect' && !report);
}
function reset() {
  report = null; selected = null;
  for (const key of Object.keys(overrides)) delete overrides[key];
  for (const key of Object.keys(replacements)) delete replacements[key];
  $('paragraphList').replaceChildren(); $('editPane').hidden = true;
  $('warnings').textContent = 'Проверьте документ, чтобы увидеть структуру.';
  $('summary').textContent = 'Содержимое остаётся только в выбранном файле.';
  $('changeCounter').textContent = '0 правок'; busy(false);
}
$('sourceFile').addEventListener('change', () => {
  source = $('sourceFile').files[0] || null; reset();
  $('fileLabel').textContent = source ? source.name + ' · ' + (source.size / 1048576).toFixed(2) + ' МиБ' : 'Файл не выбран';
  if (source) $('outputName').value = source.name.replace(/\.(docx|pdf)$/i, '') + ' — редакция';
});
function changeCount() {
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
    return;
  }
  for (const item of report.paragraphs || []) {
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
  changeCount(); drawRows(); tell('Правка подготовлена. Для создания новой редакции нажмите «Скачать».');
});
$('resetParagraph').addEventListener('click', () => {
  if (selected === null) return;
  delete overrides[selected]; delete replacements[selected];
  changeCount(); editParagraph(selected); tell('Локальная правка сброшена.');
});
async function inspect() {
  if (!source) return;
  busy(true); $('warnings').textContent = 'Проверяем структуру файла…';
  try {
    const data = new FormData(); data.append('file', source, source.name);
    const response = await fetch('/api/docwork/inspect', {method:'POST',body:data,cache:'no-store'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'HTTP '+response.status);
    report = result; selected = null; $('editPane').hidden = true;
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
async function download(target) {
  if (!source || !report) return;
  busy(true);
  try {
    const options = {preset:$('preset').value, output_name:$('outputName').value,
      auto_headings:$('autoHeadings').checked, clear_highlights:$('clearHighlights').checked,
      hide_proofing:$('hideProofing').checked, heading_bold:$('headingBold').checked,
      body_font:$('bodyFont').value, chapter_font:$('chapterFont').value,
      subchapter_font:$('subchapterFont').value,
      body_size:$('bodySize').value, chapter_size:$('chapterSize').value,
      subchapter_size:$('subchapterSize').value,
      overrides, replacements};
    const form = new FormData(); form.append('file', source, source.name);
    form.append('target', target); form.append('options', JSON.stringify(options));
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
    tell('Файл создан: '+name);
  } catch (e) {tell('Конвертация не удалась: '+e.message, true);}
  finally {busy(false);}
}
$('downloadDocx').addEventListener('click', () => download('docx'));
$('downloadPdf').addEventListener('click', () => download('pdf'));
})();
