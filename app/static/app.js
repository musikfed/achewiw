const $=s=>document.querySelector(s);
let project=null, projects=[], pending=null, model='qwen3:8b', isBusy=false, previousSceneId=null;
let activeGenerationJobId=null, refreshGenerationBusy=false, shownAuditJobId=null;
let draftSaveTimer=null;
let bookJobId=null, bookPollBusy=false;
let bookDraftMode=false, bookDraftBusy=false, bookDraftWords=-1, bookDraftFetchedAt=0;
const escaped=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let activeKind='all';
// Browser UI state is separate from SQLite manuscript data.
const safeGet=(storage,key)=>{try{return storage.getItem(key)}catch{return null}};
const safeSet=(storage,key,value)=>{try{storage.setItem(key,value)}catch{}};
const uiKey=(name,id)=>'achewiw.v8.'+name+'.'+(id||project?.id||'none');
function stashUnsavedProject(){
  if(!project)return;
  updateBrief();collectScenes();collectText();
  const data={version:1,server_updated_at:project.updated_at,project};
  try{sessionStorage.setItem(uiKey('manuscriptDraft'),JSON.stringify(data))}catch{}
}
function queueUnsavedProjectSave(){
  if(!project)return;
  clearTimeout(draftSaveTimer);
  draftSaveTimer=setTimeout(()=>{
    draftSaveTimer=null;
    if(project)stashUnsavedProject();
  },350);
}
function recoverUnsavedProject(serverProject){
  const raw=safeGet(sessionStorage,uiKey('manuscriptDraft',serverProject.id));
  if(!raw)return serverProject;
  try{
    const item=JSON.parse(raw);
    if(item.version!==1||item.project?.id!==serverProject.id)return serverProject;
    if(item.server_updated_at!==serverProject.updated_at){
      toast('В серверной рукописи появились новые правки; старый черновик из этой вкладки не применён.');
      return serverProject;
    }
    if(JSON.stringify(item.project)!==JSON.stringify(serverProject)){
      toast('Восстановлены несохранённые изменения из этой вкладки. Нажми «Сохранить».');
      return item.project;
    }
  }catch{}
  return serverProject;
}
const workModes={
 fiction:{name:'Фантастика и проза',part:'Сцена',eyebrow:'СТУДИЯ ФАНТАСТИКИ / РОМАН / ПРОЗА',intro:'Канон мира, повороты сюжета, последовательность сцен и литературная редактура.',structure:'Сюжет и главы',write:'Редактор художественного текста',audit:'Редакторская проверка истории'},
 technical:{name:'Техническая книга',part:'Раздел',eyebrow:'НАУКА / ТЕХНИКА / ИНЖЕНЕРНАЯ КНИГА',intro:'Тезисы, проверяемые источники, терминология, технические разделы и полноценное издание.',structure:'Разделы и доказательства',write:'Редактор технических глав',audit:'Проверка фактов и источников'},
 speech:{name:'Выступление',part:'Блок речи',eyebrow:'ВЫСТУПЛЕНИЕ / ДОКЛАД / ЛЕКЦИЯ',intro:'Длительность, аудитория, речевые блоки, паузы и чистовой текст для сцены.',structure:'План выступления',write:'Редактор выступления',audit:'Репетиция и проверка речи'},
 nonfiction:{name:'Нонфикшн',part:'Глава',eyebrow:'НОНФИКШН / ОБРАЗОВАНИЕ / ИССЛЕДОВАНИЕ',intro:'Ясная структура, доказательные аргументы, реальные источники и читаемая книга.',structure:'Главы и аргументы',write:'Редактор нонфикшн',audit:'Логическая и фактологическая проверка'}
};
const modelPicks={
 fiction:{name:'qwen3:8b',reason:'Фантастика: qwen3:8b — хороший баланс русского текста, скорости и VRAM. Для сравнения художественного стиля попробуйте Hermes-4-14B.'},
 technical:{name:'qwen2.5-coder:14b',reason:'Технические главы и код: qwen2.5-coder:14b, если хватает VRAM. Проверка фактов и DOI остаётся за автором.'},
 speech:{name:'qwen3:8b',reason:'Выступление: qwen3:8b для структуры, ясных переходов и репетиций.'},
 nonfiction:{name:'qwen3:8b',reason:'Нонфикшн: qwen3:8b, но библиографию и источники нужно проверять.'}
};
function recommendModel(choose=false){
 const kind=project?.work_type||'fiction',recommend=modelPicks[kind]||modelPicks.fiction;
 $('#modelGuide').textContent=recommend.reason;
 const select=$('#model');
 if(choose && Array.from(select.options).some(o=>o.value===recommend.name)){
  model=recommend.name;select.value=recommend.name;
 }
}
function setKindFilter(kind){
 if(kind!=='all'&&!workModes[kind])return;
 activeKind=kind;safeSet(localStorage,'achewiw.v8.kindFilter',kind);
 document.querySelectorAll('[data-kind-filter]').forEach(b=>b.classList.toggle('selected',b.dataset.kindFilter===kind));
 renderProjects();
}
function updateWorkCopy(){
 const kind=project?.work_type||'fiction',copy=workModes[kind]||workModes.fiction;
 $('#workEyebrow').textContent=copy.eyebrow;
 $('#workIntro').textContent=copy.intro;
 $('#structureHeading').textContent=copy.structure;
 $('#writingHeading').textContent=copy.write;
 $('#auditHeading').textContent=copy.audit;
 $('#writingSectionLabel').textContent=copy.part;
 $('#addScene').textContent='+ Добавить: '+copy.part.toLowerCase();
 $('#generateScene').textContent='✦ Предложить текст: '+copy.part.toLowerCase();
 $('#buildOutline').textContent=kind==='speech'?'✦ Создать план выступления':'✦ Составить план';
 $('#buildOutline2').textContent=kind==='speech'?'✦ Составить тайминг':'✦ Предложить структуру';
 $('#runAudit').textContent=kind==='technical'?'✦ Проверить тезисы и источники':
   kind==='speech'?'✦ Проверить речь и тайминг':
   kind==='nonfiction'?'✦ Редакторский фактчекинг':'✦ Литературный аудит';
 $('#aiGoStructure').textContent='02 · Исправить: '+copy.part.toLowerCase();

 $('#structureHint').textContent=kind==='technical'?'Каждый раздел содержит задачу, понятия, аргументацию и проверяемые источники.':kind==='speech'?'Каждый блок речи имеет переходы, тайминг и ясную задачу.':'Создавайте разделы; текст публикуется только после сохранения.';
 $('#writingHint').textContent=kind==='speech'?'Речь сохраняется блоками; ИИ предлагает текст, но автор принимает каждую правку.':'Сохранённый текст является рукописью, а предложения ИИ не подменяют книгу.';
 const names={brief:'01 · Задание',structure:'02 · План',write:'03 · Редактор',illustrations:'04 · Иллюстрации',audit:'05 · Проверка',book:'06 · Типография / Экспорт'};
 document.querySelectorAll('[data-tab]').forEach(b=>{if(names[b.dataset.tab])b.textContent=names[b.dataset.tab]});
 document.querySelectorAll('[data-only-kind]').forEach(el=>el.hidden=!el.dataset.onlyKind.split(' ').includes(kind));
}
async function api(path,options={}){const r=await fetch('/api'+path,{headers:{'Content-Type':'application/json'},...options});if(!r.ok){let detail='Ошибка '+r.status;try{detail=(await r.json()).detail||detail}catch{}throw Error(detail)}if(r.status===204)return null;return r.json()}
function renderAIText(source){
  // Escape BEFORE inserting typographic markup; never execute generated HTML/JS.
  let markup=escaped(source||'').replace(/\r/g,'');
  markup=markup.replace(/^######\s+(.+)$/gm,'<h5>$1</h5>')
    .replace(/^#####\s+(.+)$/gm,'<h5>$1</h5>')
    .replace(/^####\s+(.+)$/gm,'<h4>$1</h4>')
    .replace(/^###\s+(.+)$/gm,'<h3>$1</h3>')
    .replace(/^##\s+(.+)$/gm,'<h2>$1</h2>')
    .replace(/^#\s+(.+)$/gm,'<h2>$1</h2>')
    .replace(/\*\*([^\n*]+)\*\*/g,'<strong>$1</strong>')
    .replace(/(^|[^*])\*([^\n*]+)\*/g,'$1<em>$2</em>')
    .replace(/^(?:---+|___+)$/gm,'<hr/>');
  return markup.split(/\n\s*\n/).map(part=>{
    const lines=part.split('\n').filter(x=>x.trim());
    if(!lines.length)return '';
    if(lines.length===1 && /^<(?:h[2-5]|hr)/.test(lines[0]))return lines[0];
    if(lines.every(line=>/^\s*[-•]\s+/.test(line)))return '<ul>'+lines.map(line=>'<li>'+line.replace(/^\s*[-•]\s+/,'')+'</li>').join('')+'</ul>';
    return '<p>'+lines.join('<br/>')+'</p>';
  }).join('\n');
}
function toast(message){const t=$('#toast');t.textContent=message;t.style.display='block';clearTimeout(toast.timer);toast.timer=setTimeout(()=>t.style.display='none',5000)}
function setBusy(value){isBusy=value;document.querySelectorAll('button').forEach(b=>{if(['generateScene','buildOutline','buildOutline2','runAudit','unloadModel'].includes(b.id))b.disabled=value});$('#status').textContent=value?'Идёт генерация StoryEngine · сохраняется на сервере':'Студия готова'}
function renderProjects(){
 const items=projects.filter(p=>activeKind==='all'||(p.work_type||'fiction')===activeKind);
 $('#projects').innerHTML=items.map(p=>'<button class="projectItem '+(project?.id===p.id?'selected':'')+'" data-id="'+escaped(p.id)+'"><strong>'+escaped(p.title)+'</strong><small>'+escaped(workModes[p.work_type||'fiction']?.name||'Проза')+'</small></button>').join('');
 document.querySelectorAll('.projectItem').forEach(b=>b.onclick=()=>loadProject(b.dataset.id))
}
async function refreshProjects(){projects=await api('/projects');renderProjects()}
function render(){
 const active=!!project;$('#editor').hidden=!active;$('#empty').hidden=active;
 $('#heading').textContent=active?project.title:'Выберите мастерскую';
 updateWorkCopy();if(!active)return;
 document.querySelectorAll('[data-field]').forEach(el=>el.value=project[el.dataset.field]??'');
 $('#export').href='/api/projects/'+project.id+'/export';
 $('#meta').textContent=project.scenes.length+' частей';
 const prefix='/api/projects/'+project.id+'/manuscript';
 for(const [id,suffix] of Object.entries({bookMarkdown:'.md',bookEpub:'.epub',bookFb2:'.fb2',bookPdf:'.pdf',bookPrint:'.print.pdf',bookDocx:'.docx'}))$('#'+id).href=prefix+suffix;
 updateWorkCopy();recommendModel();renderProjects();renderScenes();renderWriting();
}
function updateBrief(){document.querySelectorAll('[data-field]').forEach(el=>{project[el.dataset.field]=el.value})}
async function save(){if(!project)return;clearTimeout(draftSaveTimer);draftSaveTimer=null;updateBrief();collectScenes();collectText();const saved=await api('/projects/'+project.id,{method:'PUT',body:JSON.stringify(project)});project=saved;try{sessionStorage.removeItem(uiKey('manuscriptDraft'))}catch{}await refreshProjects();$('#saveState').textContent='Сохранено · '+new Date().toLocaleTimeString();render();toast('Проект сохранён')}
async function loadProject(id){if(isBusy)return;clearTimeout(draftSaveTimer);draftSaveTimer=null;if(project)stashUnsavedProject();const saved=await api('/projects/'+id);project=recoverUnsavedProject(saved);safeSet(localStorage,'achewiw.activeProjectId',id);pending=null;bookJobId=null;bookDraftMode=false;bookDraftWords=-1;render();recommendModel(false);const tab=safeGet(localStorage,uiKey('tab',id));if(['brief','structure','write','illustrations','audit','book'].includes(tab))showTab(tab);await restoreLatestGeneration();await restoreLatestBook();const scroll=safeGet(sessionStorage,uiKey('scroll',id));if(scroll!==null&&Number.isFinite(Number(scroll))){requestAnimationFrame(()=>requestAnimationFrame(()=>window.scrollTo({top:Math.max(0,Number(scroll)),behavior:'instant'})))}}
async function createProject(kind){
 if(isBusy)return;
 const selected=workModes[kind]?kind:(activeKind==='all'?'fiction':activeKind);
 const title=prompt('Название: '+workModes[selected].name,'Новая работа');
 if(title===null||!title.trim())return;
 project=await api('/projects',{method:'POST',body:JSON.stringify({title:title.trim(),work_type:selected})});
 activeKind=selected;document.querySelectorAll('[data-kind-filter]').forEach(b=>b.classList.toggle('selected',b.dataset.kindFilter===selected));
 pending=null;activeGenerationJobId=null;$('#aiPanel').hidden=true;
 await refreshProjects();render();showTab('brief');
}
async function deleteCurrentProject(){
 if(!project || isBusy)return;
 const current=project;
 if(!confirm('Удалить только «'+current.title+'» из библиотеки? Перед удалением SQLite сохранится в data/backups/. Другие работы не затрагиваются.'))return;
 const phrase=prompt('Для подтверждения точно введи название работы:', '');
 if(phrase!==current.title){toast('Удаление отменено');return;}
 try{
  await api('/projects/'+current.id,{method:'DELETE'});
  project=null;pending=null;activeGenerationJobId=null;
  await refreshProjects();render();
  toast('Работа удалена. Резервная копия SQLite сохранена.');
 }catch(e){toast('Удаление не выполнено: '+e.message)}
}
function showTab(name){document.querySelectorAll('.tabpane').forEach(n=>n.hidden=n.id!==name);document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.tab===name));if(project)safeSet(localStorage,uiKey('tab'),name);if(name==='audit'){renderStats();renderAuditHistory()}if(name==='book'){renderBook();if(bookJobId)refreshFullBook();}document.dispatchEvent(new CustomEvent('achewiw:tab-shown',{detail:{name}}))}
async function renderAuditHistory(){
  if(!project)return;
  const target=$('#auditHistory');
  const projectId=project.id;
  target.textContent='Получаем историю QA из SQLite…';
  try{
    const reports=await Promise.all(['audit','continuity'].map(mode=>
      api('/projects/'+projectId+'/ai/jobs?mode='+mode+'&summary=true',{cache:'no-store'})));
    const jobs=reports.flat().sort((a,b)=>Date.parse(b.updated_at)-Date.parse(a.updated_at)).slice(0,30);
    if(!project || projectId!==project.id)return;
    target.replaceChildren();
    if(!jobs.length){target.textContent='Ещё нет QA-отчётов для этой истории.';return}
    for(const job of jobs){
      const row=document.createElement('button');
      row.type='button';
      row.className='audit-history-item';
      row.textContent=new Date(job.updated_at).toLocaleString()+' · '+
        (job.status==='ready'?'Отчёт готов':job.status)+
        ' · '+({
          in_progress:'правки в работе',completed:'исправлено',reference:'справка',
          unreviewed:'решение не принято'
        }[job.author_decision]||'');
      row.onclick=async()=>{
        if(isBusy){toast('Сначала дождись окончания текущей генерации');return}
        try{
          const report=await api('/ai/jobs/'+job.id,{cache:'no-store'});
          renderGeneration(report);
          $('#aiPanel').scrollIntoView({behavior:'smooth',block:'start'});
        }catch(e){toast('Не удалось открыть QA-отчёт: '+e.message)}
      };
      target.append(row);
    }
  }catch(e){target.textContent='Ошибка истории QA: '+e.message}
}
async function renderBook(){
  if(!project)return;
  const container=$('#bookPreview');
  container.replaceChildren();
  $('#bookSummary').textContent='Читаем сохранённую рукопись…';
  for(const id of ['bookMarkdown','bookEpub','bookFb2','bookPdf','bookPrint','bookDocx'])$('#'+id).hidden=true;
  try{
    const data=await api('/projects/'+project.id+'/manuscript',{cache:'no-store'});
    const artwork=await api('/projects/'+project.id+'/illustrations',{cache:'no-store'});
    $('#bookSummary').textContent=data.written_scenes+' из '+data.total_scenes+
      ' частей написано · '+data.total_words+' слов';
    const missing=data.unwritten.map(ch=>ch.index+'. '+ch.title);
    $('#bookWarning').textContent=missing.length?
      'Ещё не написаны части: '+missing.join(' · ')+
      '. Открой 03 · Редактор, допиши и сохрани. Экспорт включает только готовые тексты.':
      'Все части содержат текст. Проверь последовательность и скачай книгу.';
    const h=document.createElement('h2');
    h.textContent=data.title;
    container.append(h);
    if(!data.chapters.length){
      const p=document.createElement('p');
      p.className='book-empty';
      p.textContent=data.total_scenes?
        'План существует, но текст разделов ещё не написан. Начни со сцены 1 в редакторе или сгенерируй её текст и сохрани. QA-анализ не является книгой.':
        'Пока нет ни одного раздела. Сначала открой 02 · Story.prj и создай каркас истории.';
      container.append(p);
      return;
    }
    for(const ch of data.chapters){
      const chapter=document.createElement('section');
      chapter.className='book-chapter';
      const title=document.createElement('h3');
      title.textContent=ch.index+'. '+ch.title;
      chapter.append(title);
      const chapterArt=artwork.filter(a=>a.scene_id===ch.scene_id);
      const appendFigures=(placement)=>{
        for(const a of chapterArt.filter(x=>x.placement===placement)){
          const figure=document.createElement('figure');
          const img=document.createElement('img');
          const caption=document.createElement('figcaption');
          img.src='/api/projects/'+project.id+'/illustrations/'+a.id+'.png';
          img.alt=a.caption||'Иллюстрация главы';
          img.loading='lazy';
          caption.textContent=a.caption;
          figure.append(img,caption);
          chapter.append(figure);
        }
      };
      appendFigures('start');
      for(const paragraph of ch.text.split(/\n\s*\n/)){
        if(!paragraph.trim())continue;
        const p=document.createElement('p');
        p.textContent=paragraph.trim();
        chapter.append(p);
      }
      appendFigures('end');
      container.append(chapter);
    }
    for(const id of ['bookMarkdown','bookEpub','bookFb2','bookPdf','bookPrint','bookDocx'])$('#'+id).hidden=false;
  }catch(e){
    $('#bookSummary').textContent='Не удалось загрузить рукопись';
    $('#bookWarning').textContent=e.message;
  }
}
function gotoTab(name){
  if(!project)return;
  updateBrief();collectScenes();collectText();
  showTab(name);
  document.querySelector('.tabs').scrollIntoView({behavior:'smooth',block:'start'});
}
async function saveAndReadBook(){
  if(!project)return;
  collectScenes();collectText();
  try{await save();gotoTab('book')}catch(e){toast('Не удалось сохранить книгу: '+e.message)}
}
async function saveAuditDecision(){
  if(!shownAuditJobId)return;
  const button=$('#saveAuditDecision');
  button.disabled=true;
  try{
    const result=await api('/ai/jobs/'+shownAuditJobId+'/decision',{
      method:'PUT',
      body:JSON.stringify({
        decision:$('#auditDecision').value,
        notes:$('#auditNotes').value
      })
    });
    $('#auditDecisionStatus').textContent='Решение сохранено · '+new Date(result.reviewed_at).toLocaleString()+
      '. Сцены и книга не изменены автоматически.';
    toast('Решение по редакторскому отчёту сохранено в SQLite');
  }catch(e){toast('Ошибка сохранения решения: '+e.message)}
  finally{button.disabled=false}
}
function renderScenes(){
 const type=project.work_type||'fiction',unit=workModes[type]?.part||'Сцена';
 const fields=type==='technical'?[
  ['title','Название раздела'],['active_objects','Ключевые термины (через запятую)'],
  ['anchor','Проверяемый тезис / задача'],['tempo','Уровень сложности'],
  ['reader_info','Доказательства и источники'],['hook','Что проверить перед выпуском']
 ]:type==='speech'?[
  ['title','Название части речи'],['active_objects','Ключевые тезисы'],
  ['anchor','Цель выступления'],['tempo','Темп и минуты'],['reader_info','Переходы и аудитория'],
  ['hook','Финальная фраза / приглашение к действию']
 ]:type==='nonfiction'?[
  ['title','Название главы'],['active_objects','Главные идеи'],
  ['anchor','Основной тезис'],['tempo','Сложность'],['reader_info','Примеры, ссылки, подтверждения'],
  ['hook','Вывод главы']
 ]:[
  ['title','Название'],['active_objects','Активные объекты (через запятую)'],
  ['anchor','Якорь-напоминание'],['tempo','Темп'],['reader_info','Информация / подсказки'],
  ['hook','Крючок в конце']
 ];
 $('#sceneCards').innerHTML=project.scenes.map((s,i)=>
  '<article class="scene" data-index="'+i+'"><div class="sceneTop"><strong>'+
  escaped(unit)+' '+(i+1)+'</strong><button class="removeScene" data-index="'+i+'">Удалить</button></div>'+
  '<div class="sceneFields">'+fields.map(([field,label])=>
    '<label>'+label+(field==='reader_info'||field==='hook'?
    '<textarea data-scene-field="'+field+'" rows="2">'+escaped(s[field])+'</textarea>':
    '<input data-scene-field="'+field+'" value="'+escaped(Array.isArray(s[field])?s[field].join(', '):s[field])+'">')+
    '</label>').join('')+'</div></article>').join('');
 document.querySelectorAll('.removeScene').forEach(b=>b.onclick=()=>{
  collectScenes();if(confirm('Удалить '+unit.toLowerCase()+'?')){
    project.scenes.splice(Number(b.dataset.index),1);renderScenes();renderWriting();
  }
 });
}
function collectScenes(){document.querySelectorAll('.scene').forEach(card=>{const s=project.scenes[Number(card.dataset.index)];card.querySelectorAll('[data-scene-field]').forEach(el=>{s[el.dataset.sceneField]=el.dataset.sceneField==='active_objects'?el.value.split(',').map(x=>x.trim()).filter(Boolean):el.value})})}
function renderWriting(){const select=$('#writingScene'),old=select.value;select.innerHTML=project.scenes.map(s=>'<option value="'+escaped(s.id)+'">'+escaped(s.title)+'</option>').join('');const stored=safeGet(localStorage,uiKey('writingScene'));const chosen=[old,stored].find(id=>project.scenes.some(s=>s.id===id));if(chosen)select.value=chosen;switchWriting()}
function switchWriting(){const s=project.scenes.find(s=>s.id===$('#writingScene').value);$('#storyText').value=s?.text??'';previousSceneId=s?.id??null;if(s)safeSet(localStorage,uiKey('writingScene'),s.id)}
function collectText(){const s=project.scenes.find(s=>s.id===previousSceneId);if(s)s.text=$('#storyText').value}
async function renderStats(){
 if(!project)return;
 try{
  const data=await api('/projects/'+project.id+'/stats');
  const isStory=(project.work_type||'fiction')==='fiction';
  $('#stats').innerHTML='<h3>Объём: '+data.total_words+' слов · ≈ '+data.minutes_read_aloud+
    ' мин произнесения (150 слов/мин)' + (data.target_words?' · цель: '+data.target_words+' слов · выполнено '+data.completion_percent+'% · осталось '+data.remaining_words+' слов':'')+'</h3>'+
    data.scenes.map(s=>'<div class="stat"><strong>'+escaped(s.title)+'</strong><span>'+
    s.words+' слов · '+s.minutes_read_aloud+' мин'+
    (isStory?' · '+s.active_objects+' сущностей'+(s.overloaded?' ⚠ перегруз':' ✓'):'')+
    '</span></div>').join('');
 }catch(e){toast(e.message)}
}
function renderGeneration(job){
  if(!project || job.project_id!==project.id)return;
  const ongoing=['queued','running'].includes(job.status);
  setBusy(ongoing);
  $('#aiPanel').hidden=false;
  $('#aiHeading').textContent=['audit','continuity'].includes(job.mode)?
    '✦ Редакторская проверка причинности · не текст издания':
    job.mode==='polish'?'✦ Литературная редактура · без ИИ-штампов':
    job.mode==='block'?'✦ Фрагмент рассказа · предложение автора':
    job.mode==='outline'?'✦ Предложение структуры: '+(workModes[project.work_type]?.name||'книга'):
    '✦ Черновик: '+(workModes[project.work_type]?.part||'глава');
  $('#aiStage').textContent=job.status==='ready'&&['audit','continuity'].includes(job.mode)?'QA-отчёт готов · выбери решение автора ниже':job.stage||'Ожидание генерации';
  $('#aiJobId').textContent='Задание '+job.id.slice(0,12)+' · '+job.model+' · '+new Date(job.updated_at).toLocaleTimeString();
  if(job.status==='ready' && job.result){
    $('#aiOutput').innerHTML=renderAIText(job.result);
  }else{
    $('#aiOutput').textContent=job.result||'Ожидание первого фрагмента текста от Ollama…';
  }
  const progress=$('#aiProgress');
  if(Number.isFinite(job.percent))progress.value=job.percent;
  else progress.removeAttribute('value');
  $('#aiPercent').textContent=job.status==='ready'?'100% · готово':job.percent==null?'Ожидание токенов':job.percent+'% к лимиту токенов';
  const secs=job.eta_seconds;
  $('#aiEta').textContent=ongoing?
    (secs!=null?'Ориентир до лимита ответа: ~'+Math.max(1,Math.ceil(secs/60))+' мин. ':'Время пока неизвестно. ')+
    'Процент и ETA приблизительные; модель может закончить значительно раньше.':
    'Получено примерно '+(job.estimated_tokens||0)+' токенов. История хранится в SQLite.';
  $('#aiNotice').textContent=ongoing?
    'Ответ поступает частями и сохраняется на сервере. Можно обновить страницу — запись не пропадёт.':
    job.status==='ready'?
      (['audit','continuity'].includes(job.mode)?
        'QA-отчёт сохранён. Выбери решение по рекомендациям, затем исправляй нужные сцены. Сам отчёт НЕ является книгой.':
        job.parse_error?'Ответ готов, но JSON-каркас не разобран: '+job.parse_error:
        'Черновик готов. Исходный проект не изменён до принятия предложения.'):
      'Генерация '+(job.status==='interrupted'?'прервана':'завершилась ошибкой')+
        '. Полученный текст сохранён. '+(job.error||'');
  if(job.status==='ready' && job.mode==='block' && job.result){
    const scene=project.scenes.find(s=>s.id===job.scene_id);
    const block=scene?.blocks?.find(b=>b.id===job.block_id);
    const actual=(job.result.match(/[\p{L}\p{N}]+(?:[-’'][\p{L}\p{N}]+)*/gu)||[]).length;
    const target=block?.target_words;
    const deviation=target?Math.round((actual-target)/target*100):null;
    $('#aiNotice').textContent+=' Получено '+actual+' слов'+
      (target?' при цели '+target+' ('+(deviation>0?'+':'')+deviation+'%)'+
       (Math.abs(deviation)>15?' · объём вне допуска ±15%, решает автор':''):'')+
      '. Ничего не добавлено в рукопись без принятия.';
  }
  if(job.status==='ready' && job.mode==='polish'){
    $('#aiNotice').textContent+=' Сравни новую формулировку с исходником: модель может незаметно менять смысл.';
  }
  pending=job.status==='ready'?{
    mode:job.mode,model:job.model,result:job.result,
    suggested_scenes:job.suggested_scenes,sceneId:job.scene_id,
    jobId:job.id,blockId:job.block_id,accepted:job.accepted
  }:null;
  $('#applyAI').hidden=!(pending&&!pending.accepted&&(pending.mode==='scene'||pending.mode==='block'||pending.mode==='polish'||(pending.mode==='outline'&&pending.suggested_scenes)));
  $('#aiNextSteps').hidden=job.status!=='ready';
  const review=job.status==='ready'&&['audit','continuity'].includes(job.mode);
  $('#auditReview').hidden=!review;
  shownAuditJobId=review?job.id:null;
  if(review){
    $('#auditDecision').value=job.author_decision||'unreviewed';
    $('#auditNotes').value=job.author_notes||'';
    $('#auditDecisionStatus').textContent=job.reviewed_at?
      'Решение сохранено · '+new Date(job.reviewed_at).toLocaleString():
      'Отчёт сохранён, но решение автора ещё не указано.';
    $('#downloadAudit').href='/api/ai/jobs/'+job.id+'/report.md';
    if(document.querySelector('#audit').hidden===false)renderAuditHistory();
  }
  if(!ongoing)activeGenerationJobId=null;
}
async function refreshGeneration(){
  if(!project||!activeGenerationJobId||refreshGenerationBusy)return;
  refreshGenerationBusy=true;
  try{
    const job=await api('/ai/jobs/'+activeGenerationJobId,{cache:'no-store'});
    renderGeneration(job);
  }catch(e){$('#aiNotice').textContent='Связь с сервером потеряна: '+e.message+'. Черновик остаётся в базе.'}
  finally{refreshGenerationBusy=false}
}
async function restoreLatestGeneration(){
  if(!project)return;
  try{
    const jobs=await api('/projects/'+project.id+'/ai/jobs',{cache:'no-store'});
    if(jobs.length){
      const latest=jobs[0];
      activeGenerationJobId=['queued','running'].includes(latest.status)?latest.id:null;
      renderGeneration(latest);
    } else {
      $('#aiPanel').hidden=true;
      activeGenerationJobId=null;
      setBusy(false);
    }
  }catch(e){toast('Не удалось восстановить журнал генерации: '+e.message)}
}

function setBookReaderMode(draft){
 bookDraftMode=!!draft && !!bookJobId;
 $('#bookPreview').hidden=bookDraftMode;
 $('#bookDraftPreview').hidden=!bookDraftMode;
 $('#bookPreviewLabel').hidden=bookDraftMode;
 $('#bookViewDraft').hidden=!bookJobId||bookDraftMode;
 $('#bookViewSaved').hidden=!bookJobId||!bookDraftMode;
 $('#bookDraftRefresh').hidden=!bookDraftMode;
 if(bookDraftMode)refreshBookDraft(true);
}
async function refreshBookDraft(force=false){
 if(!project||!bookJobId||bookDraftBusy||$('#book').hidden||!bookDraftMode)return;
 const stamp=Date.now();
 if(!force && stamp-bookDraftFetchedAt<12000)return;
 const jobId=bookJobId, projectId=project.id;
 bookDraftBusy=true;
 bookDraftFetchedAt=stamp;
 try{
   const data=await api('/books/jobs/'+jobId+'/draft',{cache:'no-store'});
   if(!project||project.id!==projectId||bookJobId!==jobId||!bookDraftMode)return;
   const reader=$('#bookDraftPreview');
   reader.replaceChildren();
   const title=document.createElement('h2');title.textContent=data.title;
   reader.append(title);
   const notice=document.createElement('p');
   notice.className='book-empty';
   notice.textContent='ЧЕРНОВИК ИИ · '+data.book.generated_words.toLocaleString('ru-RU')+
     ' из '+data.book.target_words.toLocaleString('ru-RU')+
     ' слов ('+data.book.percent_words+'%). Это реальные написанные главы. '+
     'Они не попадут в EPUB/PDF до принятия автором.';
   reader.append(notice);
   for(const chapter of data.chapters){
     const section=document.createElement('section');
     section.className='book-chapter';
     const heading=document.createElement('h3');
     heading.textContent=chapter.index+'. '+chapter.title+' · '+
       chapter.words+' / '+chapter.target_words+' слов';
     section.append(heading);
     if(!chapter.text.trim()){
       const empty=document.createElement('p');
       empty.textContent='Эта глава ожидает написания.';
       section.append(empty);
     }else{
       for(const paragraph of chapter.text.split(/\n\s*\n/)){
         if(!paragraph.trim())continue;
         const node=document.createElement('p');
         node.textContent=paragraph.trim();
         section.append(node);
       }
     }
     reader.append(section);
   }
   bookDraftWords=data.book.generated_words;
 }catch(e){
   $('#bookDraftPreview').textContent='Ошибка чтения глав: '+e.message;
 }finally{bookDraftBusy=false}
}
function updateBookProduction(job){
 const summary=job.book||{}, chapters=job.book_chapters||[];
 const accepted=chapters.length>0 && chapters.every(c=>c.accepted_at);
 const active=['queued','running'].includes(job.status);
 $('#bookGenerate').hidden=active || (!accepted&&['ready','interrupted','error'].includes(job.status));
 $('#bookResume').hidden=!['interrupted','error'].includes(job.status);
 $('#bookAccept').hidden=job.status!=='ready'||accepted;
 $('#bookGenerationProgress').value=Math.min(100,summary.percent_words||0);
 $('#bookGenerationStatus').textContent=(job.stage||job.status)+' · написано '+
   (summary.generated_words||0).toLocaleString('ru-RU')+' / '+
   (summary.target_words||0).toLocaleString('ru-RU')+
   ' слов ('+(summary.percent_words||0)+'%) · глав '+(summary.chapters_complete||0)+
   '/'+(summary.chapters||0)+(job.error?' · '+job.error:'');
 $('#bookProductionHint').textContent=accepted?
   'Все главы приняты в рукопись. Ниже — сохранённая книга, доступная для экспорта.':
   'Ниже показывается создаваемая книга, НЕ старый синопсис. Экспорт PDF/EPUB пока содержит только сохранённую авторскую рукопись. Прими готовые главы отдельной кнопкой.';
 if(accepted)bookDraftMode=false;
 setBookReaderMode(bookDraftMode);
}

function renderFullBookJob(job){
 if(!project||job.project_id!==project.id)return;
 const changedJob=bookJobId!==job.id;
 bookJobId=job.id;
 if(changedJob){bookDraftWords=-1;bookDraftMode=true;}
 safeSet(localStorage,uiKey('bookJob'),job.id);
 const b=job.book||{}, chapters=job.book_chapters||[];
 const active=['queued','running'].includes(job.status);
 const accepted=chapters.length&&chapters.every(c=>c.accepted_at);
 $('#generateFullBook').disabled=active||isBusy;
 $('#resumeFullBook').hidden=!['interrupted','error'].includes(job.status);
 $('#acceptFullBook').hidden=job.status!=='ready'||!!accepted;
 const progress=$('#fullBookProgress');
 progress.hidden=false;progress.value=Math.min(100,b.percent_words||0);
 $('#fullBookStatus').textContent=
   (job.stage||job.status)+' · '+(b.generated_words||0).toLocaleString('ru-RU')+
   ' / '+(b.target_words||0).toLocaleString('ru-RU')+
   ' слов ('+(b.percent_words||0)+'%); глав: '+(b.chapters_complete||0)+'/'+(b.chapters||0)+
   (job.error?' · '+job.error:'')+
   (accepted?' · Принято в рукопись.':' · Черновики отдельно от рукописи.');
 updateBookProduction(job);
 if(bookDraftMode && (changedJob||bookDraftWords!==b.generated_words))refreshBookDraft();
 $('#fullBookChapters').replaceChildren(...chapters.map(c=>{
   const p=document.createElement('p');
   p.textContent=(c.scene_index+1)+'. '+c.title+': '+c.word_count+' / '+c.target_words+
      ' слов · '+(c.accepted_at?'принято':c.complete?'готово':'черновик, '+c.attempts+' фрагментов');
   return p;
 }));
}
async function refreshFullBook(){
 if(!project||!bookJobId||bookPollBusy)return;
 bookPollBusy=true;
 try{
   const job=await api('/books/jobs/'+bookJobId,{cache:'no-store'});
   renderFullBookJob(job);
 }catch(e){$('#fullBookStatus').textContent='Не удалось проверить книгу: '+e.message}
 finally{bookPollBusy=false}
}
async function restoreLatestBook(){
 if(!project)return;
 bookJobId=null;
 $('#fullBookStatus').textContent='Проверяем сохранённую генерацию книги…';
 const jobs=await api('/projects/'+project.id+'/ai/jobs?mode=book',{cache:'no-store'});
 if(jobs.length){
   bookJobId=jobs[0].id;
   bookDraftMode=true;
   bookDraftWords=-1;
   await refreshFullBook();
 }else{
   $('#fullBookStatus').textContent='Укажи целевой объём, утверди каркас и нажми «Написать всю книгу».';
   $('#fullBookChapters').textContent='';
   $('#fullBookProgress').hidden=true;
   $('#resumeFullBook').hidden=true;
   $('#acceptFullBook').hidden=true;
   $('#generateFullBook').disabled=false;
   $('#bookGenerate').hidden=false;
   $('#bookResume').hidden=true;
   $('#bookAccept').hidden=true;
   $('#bookGenerationProgress').value=0;
   $('#bookGenerationStatus').textContent='Генерация не запускалась. Цель: '+(project.target_length||'не указана')+
     '. Сохранённые краткие описания глав — это ещё не книга.';
   $('#bookProductionHint').textContent='Нажми «Написать полноценную книгу по каркасу» — модель напишет реальные главы по частям. Уже сохранённые тексты не будут затронуты до принятия результата.';
   setBookReaderMode(false);
 }
}
async function startFullBook(){
 if(!project||isBusy)return;
 if(!project.scenes.length){toast('Сначала создай и утверди каркас');return}
 try{
   await save();
   const job=await api('/books/generate',{method:'POST',
      body:JSON.stringify({project_id:project.id,model})});
   bookJobId=job.id;
   bookDraftMode=true;
   bookDraftWords=-1;
   renderFullBookJob(job);
   showTab('book');
   toast('Написание книги запущено. Слова и главы сохраняются на сервере.');
 }catch(e){toast('Книга не запущена: '+e.message)}
}
async function resumeFullBook(){
 if(!bookJobId)return;
 try{
   const job=await api('/books/jobs/'+bookJobId+'/resume',{method:'POST',body:'{}'});
   renderFullBookJob(job);
   showTab('book');
   toast('Генерация продолжена с сохранённого текста.');
 }catch(e){toast('Не удалось продолжить: '+e.message)}
}
async function acceptFullBook(){
 if(!bookJobId||!project)return;
 if(!confirm('Заменить тексты ВСЕХ глав готовой книгой? Исходные тексты будут сохранены как ревизии для отката.'))return;
 try{
   // Do not save stale browser copies after a server change; acceptance uses hashes.
   const result=await api('/books/jobs/'+bookJobId+'/accept',
     {method:'POST',body:JSON.stringify({confirm:true})});
   project=await api('/projects/'+project.id);
   render();await refreshFullBook();
   bookDraftMode=false;setBookReaderMode(false);await renderBook();
   toast('Принято '+result.chapters+' глав · '+result.words+' слов. Ревизии сохранены.');
 }catch(e){toast('Принятие отменено: '+e.message)}
}

async function ai(mode, extras={}){
  if(!project||isBusy)return;
  updateBrief();collectScenes();collectText();
  await save();
  if(mode==='scene'&&!$('#writingScene').value){toast('Сначала добавьте сцену');return}
  setBusy(true);
  $('#aiPanel').hidden=false;
  $('#aiOutput').textContent='Задание создаётся в базе…';
  $('#aiNotice').textContent='Подключение к Ollama…';
  $('#aiStage').textContent='Сохранение задания';
  $('#aiPercent').textContent='—';
  $('#aiProgress').removeAttribute('value');
  $('#aiEta').textContent='Ожидание начала генерации.';
  $('#applyAI').hidden=true;$('#auditReview').hidden=true;$('#aiNextSteps').hidden=true;shownAuditJobId=null;pending=null;
  try{
    const job=await api('/ai',{method:'POST',body:JSON.stringify({
      project_id:project.id,mode,model,scene_id:$('#writingScene').value||null,
      ...extras
    })});
    activeGenerationJobId=job.id;
    renderGeneration(job);
    refreshGeneration();
  }catch(e){
    $('#aiOutput').textContent=e.message;
    $('#aiNotice').textContent='Не удалось создать задание';
    setBusy(false);
  }
  $('#aiPanel').scrollIntoView({behavior:'smooth',block:'start'});
}
async function releaseSelectedModel(){
  if(isBusy){toast('Дождись завершения генерации');return}
  const selected=$('#model').value;
  if(!selected){toast('Сначала выбери модель');return}
  if(!confirm('Выгрузить из общей Ollama только модель '+selected+'?\nЭто может повлиять на другие программы, которые используют эту же модель.\nДрайвер NVIDIA, другие модели и GPU не сбрасываются.'))return;
  const button=$('#unloadModel');
  button.disabled=true;
  try{
    const result=await api('/models/unload',{method:'POST',body:JSON.stringify({
      model:selected,confirm_shared_model:true
    })});
    toast(result.status==='already_unloaded'?'Модель уже выгружена':'Ollama получила команду выгрузить '+selected);
    $('#monitorRefresh').click();
  }catch(e){toast('Не удалось освободить модель: '+e.message)}
  finally{button.disabled=isBusy}
}
async function applyAI(){
 if(!pending)return;
 if(['block','polish','scene'].includes(pending.mode)){
   const jobId=pending.jobId,mode=pending.mode,sceneId=pending.sceneId;
   if(!jobId||pending.accepted)return;
   if(!confirm(mode==='block'?
      'Добавить предложенный фрагмент в конец сцены? Внесение будет сохранено на сервере с возможностью отката.':
      mode==='polish'?
      'Заменить текст сцены литературной редактурой? Исходный текст сохранится как ревизия для отката.':
      'Заменить текст сцены новым предложением ИИ? Исходник сохранится как отдельная ревизия для отката.'))return;
   try{
     collectScenes();collectText();await save();
     const r=await api('/ai/jobs/'+jobId+'/accept',
       {method:'POST',body:JSON.stringify({confirm:true})});
     const active=project.id;
     project=await api('/projects/'+active);
     pending=null;render();showTab('write');
     $('#writingScene').value=sceneId;switchWriting();
     $('#aiPanel').hidden=true;
     toast(mode==='block'?'Фрагмент принят и сохранён · '+r.words+' слов':
       'Рукопись обновлена. Исходный текст сохранён в истории ревизий.');
   }catch(err){toast('Предложение не применено: '+err.message)}
   return;
 }
 if(pending.mode==='outline'&&pending.suggested_scenes){
   if(project.scenes.length&&!confirm('Заменить текущий каркас предложенным?'))return;
   project.scenes=pending.suggested_scenes;renderScenes();renderWriting();showTab('structure');
 }
 $('#aiPanel').hidden=true;
 toast('Предложение принято. Нажмите «Сохранить» для записи в базу.');
}
async function detectModels(){try{const data=await api('/models');const select=$('#model');select.innerHTML='';for(const m of data.models){const o=document.createElement('option');o.value=m;o.textContent=m;select.append(o)}const saved=safeGet(localStorage,'achewiw.v8.selectedModel');if(saved&&data.models.includes(saved))model=saved;else if(!data.models.includes(model))model=(data.models.includes(modelPicks[project?.work_type||'fiction']?.name)?modelPicks[project?.work_type||'fiction'].name:data.models[0]);if(model)select.value=model;recommendModel(false);$('#unloadModel').disabled=!data.models.length||isBusy;$('#ollamaStatus').textContent='● Ollama на хосте · '+data.models.length+' моделей';$('#ollamaStatus').style.color='#34865b'}catch(e){$('#ollamaStatus').textContent='⚠ '+e.message;$('#ollamaStatus').style.color='#bd6651'}}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{if(!project)return;updateBrief();collectScenes();collectText();showTab(b.dataset.tab)});
$('#newProject').onclick=$('#startProject').onclick=()=>createProject();
document.querySelectorAll('[data-create-kind]').forEach(b=>b.onclick=()=>createProject(b.dataset.createKind));
document.querySelectorAll('[data-kind-filter]').forEach(b=>b.onclick=()=>{
  const kind=b.dataset.kindFilter;setKindFilter(kind);
  const matching=kind==='all'?projects[0]:projects.find(p=>(p.work_type||'fiction')===kind);
  if(matching)loadProject(matching.id);
  else{project=null;render()}
});
$('#workType').onchange=e=>{if(!project)return;project.work_type=e.target.value;updateWorkCopy();renderProjects()};
$('#saveBrief').onclick=()=>save().catch(e=>toast(e.message));
$('#deleteProject').onclick=deleteCurrentProject;
$('#saveScenes').onclick=()=>{collectScenes();save().catch(e=>toast(e.message))};
$('#saveText').onclick=()=>{collectText();save().catch(e=>toast(e.message))};
$('#addScene').onclick=()=>{collectScenes();project.scenes.push({id:crypto.randomUUID().replaceAll('-',''),title:'Новая сцена',active_objects:[],anchor:'',tempo:'Ровный',reader_info:'',hook:'',text:''});renderScenes();renderWriting()};
$('#writingScene').onchange=()=>{collectText();switchWriting()};
$('#buildOutline').onclick=$('#buildOutline2').onclick=()=>ai('outline');
 $('#generateFullBook').onclick=startFullBook;
 $('#bookGenerate').onclick=startFullBook;
 $('#bookResume').onclick=resumeFullBook;
 $('#bookAccept').onclick=acceptFullBook;
 $('#bookViewDraft').onclick=()=>setBookReaderMode(true);
 $('#bookViewSaved').onclick=()=>setBookReaderMode(false);
 $('#bookDraftRefresh').onclick=()=>refreshBookDraft(true);
 $('#resumeFullBook').onclick=resumeFullBook;
 $('#acceptFullBook').onclick=acceptFullBook;
$('#generateScene').onclick=()=>ai('scene');
$('#runAudit').onclick=()=>ai('audit');
$('#applyAI').onclick=applyAI;
$('#saveAuditDecision').onclick=saveAuditDecision;
$('#aiGoStructure').onclick=()=>gotoTab('structure');
$('#aiGoWriting').onclick=()=>gotoTab('write');
$('#aiGoBook').onclick=()=>gotoTab('book');
$('#openBookFromAudit').onclick=()=>gotoTab('book');
$('#openBookFromEditor').onclick=saveAndReadBook;
$('#bookEdit').onclick=()=>gotoTab('write');
$('#closeAI').onclick=()=>$('#aiPanel').hidden=true;
$('#copyAI').onclick=()=>navigator.clipboard.writeText(pending?.result||$('#aiOutput').textContent).then(()=>toast('Скопировано')).catch(()=>toast('Не удалось скопировать'));
$('#model').onchange=e=>{model=e.target.value;safeSet(localStorage,'achewiw.v8.selectedModel',model)};
$('#unloadModel').onclick=releaseSelectedModel;
setInterval(()=>{if(!document.hidden){refreshGeneration();refreshFullBook()}},2000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshGeneration()});
// Keep unsaved editor fields in this browser tab across refresh without
// pushing drafts to the server. Revision matching prevents stale overwrite.
document.addEventListener('input',event=>{
  if(!project)return;
  if(event.target.closest('#editor'))stashUnsavedProject();
});
// Persist unpublished manuscript edits while typing, not just on beforeunload.
// This remains a per-tab browser draft; server canon still requires explicit Save.
for(const eventName of ['input','change']){
  document.addEventListener(eventName,event=>{
    if(event.target?.closest?.('#brief, #structure, #write'))queueUnsavedProjectSave();
  });
}
window.addEventListener('beforeunload',()=>{if(project)stashUnsavedProject()});
let lastScrollAt=0;
window.addEventListener('scroll',()=>{
  if(!project)return;
  const now=Date.now();if(now-lastScrollAt<300)return;lastScrollAt=now;
  safeSet(sessionStorage,uiKey('scroll'),String(window.scrollY));
},{passive:true});
(async()=>{try{const kind=safeGet(localStorage,'achewiw.v8.kindFilter');if(kind&&workModes[kind]||kind==='all')activeKind=kind;await refreshProjects();$('#status').textContent='Студия готова';if(projects.length){let previous=null;try{previous=localStorage.getItem('achewiw.activeProjectId')}catch{}await loadProject(projects.find(p=>p.id===previous)?.id||projects[0].id)}await detectModels()}catch(e){$('#status').textContent='Ошибка соединения';toast(e.message)}})();
