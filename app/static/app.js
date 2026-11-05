const $=s=>document.querySelector(s);
let project=null, projects=[], pending=null, model='qwen3:8b', isBusy=false, previousSceneId=null;
let activeGenerationJobId=null, refreshGenerationBusy=false, shownAuditJobId=null;
let draftSaveTimer=null;
let bookJobId=null, bookPollBusy=false;
let chapterPlan=null,storyMemory=null;
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
function updateBrief(){document.querySelectorAll('[data-field]').forEach(el=>{project[el.dataset.field]=el.dataset.field==='publication_year'?(el.value?Number(el.value):null):el.value})}
async function save(){if(!project)return;clearTimeout(draftSaveTimer);draftSaveTimer=null;updateBrief();collectScenes();collectText();const saved=await api('/projects/'+project.id,{method:'PUT',body:JSON.stringify(project)});project=saved;try{sessionStorage.removeItem(uiKey('manuscriptDraft'))}catch{}await refreshProjects();$('#saveState').textContent='Сохранено · '+new Date().toLocaleTimeString();render();toast('Проект сохранён')}
async function loadProject(id){if(isBusy)return;clearTimeout(draftSaveTimer);draftSaveTimer=null;if(project)stashUnsavedProject();const saved=await api('/projects/'+id);project=recoverUnsavedProject(saved);safeSet(localStorage,'achewiw.activeProjectId',id);pending=null;bookJobId=null;bookDraftMode=false;bookDraftWords=-1;bookDraftFetchedAt=0;render();recommendModel(false);const tab=safeGet(localStorage,uiKey('tab',id));if(['brief','structure','write','illustrations','audit','book'].includes(tab))showTab(tab);await restoreLatestGeneration();await restoreLatestBook();await loadChapterPlan();await loadStoryMemory();const scroll=safeGet(sessionStorage,uiKey('scroll',id));if(scroll!==null&&Number.isFinite(Number(scroll))){requestAnimationFrame(()=>requestAnimationFrame(()=>window.scrollTo({top:Math.max(0,Number(scroll)),behavior:'instant'})))}}
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
    $('#bookEditionStatus').textContent=
      'Автор: '+(project.author_name||'не указан')+' · год: '+(project.publication_year||'не указан')+
      ' · редакция: '+(project.edition_label||'не указана')+
      ' · ACHEWIW StoryEngine v0.6.0 · Python / FastAPI / SQLite.';
    const cover=artwork.find(a=>a.placement==='cover');
    if(cover){
      const fig=document.createElement('figure');fig.className='book-cover-preview';
      const img=document.createElement('img');img.loading='lazy';
      img.src='/api/projects/'+project.id+'/illustrations/'+cover.id+'.png';
      img.alt='Обложка книги '+data.title;
      fig.append(img);container.append(fig);
    }
    const h=document.createElement('h2');
    h.textContent=data.title;container.append(h);
    if(project.author_name){
      const author=document.createElement('p');author.className='book-author';
      author.textContent=project.author_name;container.append(author);
    }
    const year=document.createElement('p');year.className='book-edition-year';
    year.textContent=[project.edition_label,project.publisher_name,project.publication_year]
        .filter(Boolean).join(' · ');container.append(year);
    if(!data.chapters.length){
      const p=document.createElement('p');
      p.className='book-empty';
      p.textContent=data.total_scenes?
        'План существует, но текст разделов ещё не написан. Начни со сцены 1 в редакторе или сгенерируй её текст и сохрани. QA-анализ не является книгой.':
        'Пока нет ни одного раздела. Сначала открой 02 · Story.prj и создай каркас истории.';
      container.append(p);
      return;
    }
    const contents=document.createElement('nav');contents.className='book-toc';
    const t=document.createElement('h3');t.textContent='Содержание';contents.append(t);
    const ol=document.createElement('ol');
    for(const ch of data.chapters){
      const li=document.createElement('li');li.textContent=ch.index+'. '+ch.title;ol.append(li);
    }
    contents.append(ol);container.append(contents);
    for(const ch of data.chapters){
      const chapter=document.createElement('section');
      chapter.className='book-chapter';
      const title=document.createElement('h3');
      title.textContent=ch.index+'. '+ch.title;
      chapter.append(title);
      const chapterArt=artwork.filter(a=>a.scene_id===ch.scene_id);
      const appendFigure=a=>{
        const figure=document.createElement('figure');
        const img=document.createElement('img');
        const caption=document.createElement('figcaption');
        img.src='/api/projects/'+project.id+'/illustrations/'+a.id+'.png';
        img.alt=a.caption||'Иллюстрация главы';
        img.loading='lazy';
        caption.textContent=a.caption||'';
        figure.append(img,caption);chapter.append(figure);
      };
      chapterArt.filter(a=>a.placement==='start').forEach(appendFigure);
      const parts=ch.text.split(/\n\s*\n/).map(s=>s.trim()).filter(Boolean);
      parts.forEach((paragraph,i)=>{
        const p=document.createElement('p');p.textContent=paragraph;chapter.append(p);
        chapterArt.filter(a=>a.placement==='paragraph'&&a.paragraph_index===i+1)
                  .forEach(appendFigure);
      });
      chapterArt.filter(a=>a.placement==='end'||
         (a.placement==='paragraph'&&a.paragraph_index>parts.length))
         .forEach(appendFigure);
      container.append(chapter);
    }
    const credits=document.createElement('section');credits.className='book-credits';
    const sub=document.createElement('h3');sub.textContent='Выходные сведения';credits.append(sub);
    for(const line of [
      project.author_name?'Автор: '+project.author_name:'',
      project.publication_year?'Год выпуска: '+project.publication_year:'',
      project.publisher_name?'Издатель: '+project.publisher_name:'',
      project.edition_label?'Редакция: '+project.edition_label:'',
      'Создано в ACHEWIW StoryEngine v0.6.0 · Python / FastAPI / SQLite · Ollama и ComfyUI при использовании'
    ].filter(Boolean)){
      const p=document.createElement('p');p.textContent=line;credits.append(p);
    }
    container.append(credits);
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
    const latest=jobs.find(j=>j.mode!=='book');
    if(latest){
      activeGenerationJobId=['queued','running'].includes(latest.status)?latest.id:null;
      renderGeneration(latest);
    } else {
      $('#aiPanel').hidden=true;
      activeGenerationJobId=null;
      setBusy(false);
    }
  }catch(e){toast('Не удалось восстановить журнал генерации: '+e.message)}
}

function setBookReaderMode(draft, forceRefresh=true){
 bookDraftMode=!!draft && !!bookJobId;
 $('#bookPreview').hidden=bookDraftMode;
 $('#bookDraftPreview').hidden=!bookDraftMode;
 $('#bookPreviewLabel').hidden=bookDraftMode;
 $('#bookSavedToolbar').hidden=bookDraftMode;
 $('#bookPrintWarning').hidden=bookDraftMode;
 $('#bookWarning').hidden=bookDraftMode;
 $('#bookViewDraft').hidden=!bookJobId||bookDraftMode;
 $('#bookViewSaved').hidden=!bookJobId||!bookDraftMode;
 $('#bookDraftRefresh').hidden=!bookDraftMode;
 if(bookDraftMode&&forceRefresh)refreshBookDraft(true);
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
 setBookReaderMode(bookDraftMode,false);
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
 if(!active&&chapterPlan)chapterPlanTotal();
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
   bookDraftFetchedAt=0;
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


const memoryKinds={event:'Событие',character:'Персонаж',object:'Предмет',
 place:'Место',fact:'Факт',mystery:'Загадка',rule:'Правило'};
const memoryRelations={causes:'вызывает',requires:'требуется для',before:'раньше',
 foreshadows:'предвещает',reveals:'раскрывает',knows:'знает о',
 holds:'владеет',contradicts:'противоречит'};
function fillMemorySelect(id,options,selected){
 const el=$(id);const last=selected??el.value;el.replaceChildren();
 options.forEach(({value,label})=>{
   const item=document.createElement('option');item.value=value;item.textContent=label;
   el.append(item);
 });
 if([...el.options].some(o=>o.value===last))el.value=last;
}
function renderStoryMemory(data){
 storyMemory=data;
 const graph=data.audit||{};
 $('#memoryStatus').textContent='SQLite: '+(graph.nodes||0)+' узлов, '+(graph.edges||0)+
   ' связей · ветка '+(data.branch_id==='main'?'основная':'альтернативная')+
   ' · авторские факты отдельно от черновиков ИИ';
 const branchOptions=(data.branches||[]).map(b=>({
   value:b.id,label:b.name+(b.id==='main'?' (основная)':' (ветка графа)')}));
 fillMemorySelect('#memoryBranch',branchOptions,data.branch_id);
 const sceneOptions=(data.scenes||[]).map((scene,i)=>({value:scene.id,
   label:(i+1)+'. '+scene.title}));
 fillMemorySelect('#memoryNodeScene',sceneOptions);
 const graphNodes=data.nodes||[],graphEdges=data.edges||[];
 const entityOptions=graphNodes.map(n=>({value:n.id,
   label:(memoryKinds[n.kind]||n.kind)+' · '+n.title}));
 fillMemorySelect('#memoryEdgeFrom',entityOptions);
 fillMemorySelect('#memoryEdgeTo',entityOptions);
 const list=$('#memoryNodeList');list.replaceChildren();
 const header=document.createElement('h5');header.textContent='Узлы · '+graphNodes.length;list.append(header);
 graphNodes.forEach(n=>{
   const line=document.createElement('div');line.className='story-memory-item';
   const text=document.createElement('div');
   const b=document.createElement('strong');b.textContent=(memoryKinds[n.kind]||n.kind)+' · '+n.title;
   const p=document.createElement('p');p.textContent=n.statement;
   const mark=document.createElement('small');
   mark.textContent='Глава '+(n.scene_index+1)+(n.status==='outline'?' · план, не подтверждённый факт':' · закреплено автором');
   text.append(b,p,mark);line.append(text);
   if(n.status!=='outline'){
     const del=document.createElement('button');del.type='button';
     del.textContent='Удалить';del.className='danger-action';
     del.onclick=()=>deleteMemoryNode(n.id,n.title);
     line.append(del);
   }
   list.append(line);
 });
 const eList=$('#memoryEdgeList');eList.replaceChildren();
 const eh=document.createElement('h5');eh.textContent='Связи · '+graphEdges.length;eList.append(eh);
 const byId=new Map(graphNodes.map(n=>[n.id,n.title]));
 graphEdges.forEach(edge=>{
   const p=document.createElement('p');p.className='story-memory-item';
   p.textContent=(byId.get(edge.source_id)||'Удалённый узел')+' → '+
       (memoryRelations[edge.relation]||edge.relation)+' → '+
       (byId.get(edge.target_id)||'Удалённый узел');
   eList.append(p);
 });
 const open=(graph.open_mysteries||[]).map(n=>n.title);
 $('#memoryAudit').textContent='Граф QA: '+(graph.issues||[]).length+
   ' структурных проблем · нерешённых загадок '+open.length+
   (open.length?' ('+open.slice(0,8).join(', ')+')':'')+
   '. Эта проверка не доказывает литературную или фактическую правильность.';
}
async function loadStorageStatus(){
 try{
   const data=await api('/storage/status',{cache:'no-store'});
   const items=data.storage||{};
   $('#memoryStorage').textContent='SQLite · canon: '+(items.canon?.exists?'готова':'недоступна')+
     ' · search: '+(items.search?.exists?'готова':'ещё не создана')+
     ' · work: '+(items.work?.exists?'готова':'ещё не создана')+
     ' · версия '+(data.version||'?')+'. Исторические задачи сохраняются в основной БД.';
 }catch(e){$('#memoryStorage').textContent='Не удалось проверить базы: '+e.message;}
}
async function reindexStorySearch(){
 const btn=$('#memoryReindex');btn.disabled=true;
 $('#memoryStorage').textContent='Проверяем текущие версии фрагментов и обновляем только изменённые…';
 try{
   const result=await api('/storage/reindex',{method:'POST',body:'{}'});
   await loadStorageStatus();
   toast('RAG: обновлено '+result.indexed+', без изменений '+result.unchanged+
     ', устаревших записей кэша удалено '+result.removed);
 }catch(e){$('#memoryStorage').textContent='Индекс не обновлён: '+e.message;}
 finally{btn.disabled=false;}
}
async function loadStoryMemory(branch='main'){
 if(!project)return;
 const projectId=project.id;
 $('#memoryStatus').textContent='Загрузка памяти сюжета…';
 try{
   const data=await api('/projects/'+projectId+'/story-memory?branch='+encodeURIComponent(branch),
                        {cache:'no-store'});
   if(!project||projectId!==project.id)return;
   renderStoryMemory(data);
   await loadStorageStatus();
 }catch(e){
   $('#memoryStatus').textContent='Память сюжета недоступна: '+e.message;
 }
}
async function addMemoryNode(){
 if(!project||!storyMemory)return;
 const kind=$('#memoryNodeKind').value,title=$('#memoryNodeTitle').value.trim();
 const statement=$('#memoryNodeStatement').value.trim(),scene_id=$('#memoryNodeScene').value;
 if(!title||!statement){toast('Заполни название и конкретное событие/факт');return}
 try{
   await api('/projects/'+project.id+'/story-memory/nodes',{method:'POST',
      body:JSON.stringify({kind,title,statement,scene_id:scene_id||null,
                           branch_id:storyMemory.branch_id})});
   $('#memoryNodeTitle').value='';$('#memoryNodeStatement').value='';
   await loadStoryMemory(storyMemory.branch_id);
   toast('Факт записан в граф автора');
 }catch(e){toast('Не добавлено: '+e.message)}
}
async function addMemoryEdge(){
 if(!project||!storyMemory)return;
 const source_id=$('#memoryEdgeFrom').value,target_id=$('#memoryEdgeTo').value,
       relation=$('#memoryEdgeKind').value;
 if(!source_id||!target_id||source_id===target_id){
   toast('Выбери два разных узла');return;
 }
 try{
   await api('/projects/'+project.id+'/story-memory/edges',{method:'POST',
      body:JSON.stringify({source_id,target_id,relation,branch_id:storyMemory.branch_id})});
   await loadStoryMemory(storyMemory.branch_id);
   toast('Связь записана и проверена на циклы');
 }catch(e){toast('Связь не добавлена: '+e.message)}
}
async function deleteMemoryNode(id,title){
 if(!project||!storyMemory||!confirm('Удалить факт «'+title+'» и все его связи? Текст глав и каркас не изменятся.'))return;
 try{
   await api('/projects/'+project.id+'/story-memory/nodes/'+id+
     '?branch='+encodeURIComponent(storyMemory.branch_id),{method:'DELETE'});
   await loadStoryMemory(storyMemory.branch_id);
   toast('Узел графа удалён. Рукопись сохранена.');
 }catch(e){toast('Ошибка удаления узла: '+e.message)}
}
async function addMemoryBranch(){
 if(!project||!storyMemory)return;
 const name=$('#memoryBranchName').value.trim();
 if(!name){toast('Укажи название альтернативной ветки');return}
 try{
   const branch=await api('/projects/'+project.id+'/story-memory/branches',
     {method:'POST',body:JSON.stringify({name,parent_id:storyMemory.branch_id,
       fork_scene_id:$('#memoryNodeScene').value||null})});
   $('#memoryBranchName').value='';
   await loadStoryMemory(branch.id);
   toast('Создана отдельная ветка графа; основная рукопись не затронута.');
 }catch(e){toast('Ветка не создана: '+e.message)}
}

const revealChoices={
 none:'Без сюжетной тайны / без раскрытия',
 hint:'Намёк, без объяснения',
 partial:'Частичная разгадка',
 full:'Полное подготовленное раскрытие',
 aftermath:'Последствия раскрытия'
};
const tensionLabels=['0 · спокойная','1 · лёгкая','2 · интерес','3 · развитие','4 · напряжение','5 · пик'];
async function loadChapterPlan(suggested=false){
 if(!project)return;
 const projectId=project.id;
 if(!project.scenes.length){
   chapterPlan=null;
   $('#chapterPlanSummary').textContent='Сначала добавь главы в каркас.';
   $('#chapterPlanList').replaceChildren();
   return;
 }
 try{
   $('#chapterPlanSummary').textContent='Загрузка редакторского плана…';
   const plan=await api('/projects/'+projectId+'/book-plan'+(suggested?'?suggested=true':''),
                        {cache:'no-store'});
   if(!project||project.id!==projectId)return;
   chapterPlan=plan;
   renderChapterPlan();
 }catch(e){
   chapterPlan=null;
   $('#chapterPlanSummary').textContent='Не удаётся рассчитать объём глав: '+e.message;
   $('#chapterPlanList').replaceChildren();
 }
}
function collectChapterPlan(){
 if(!chapterPlan)return [];
 const rows=Array.from(document.querySelectorAll('#chapterPlanList [data-plan-scene]'));
 return rows.map((row,i)=>({
   ...chapterPlan.chapters[i],
   scene_id:row.dataset.planScene,
   target_words:Number(row.querySelector('[data-plan-words]').value),
   tension:Number(row.querySelector('[data-plan-tension]').value),
   reveal_level:row.querySelector('[data-plan-reveal]').value,
   editorial_goal:row.querySelector('[data-plan-goal]').value.trim()
 }));
}
function chapterPlanTotal(){
 if(!chapterPlan)return false;
 const list=collectChapterPlan();
 const total=list.reduce((acc,row)=>acc+(Number.isFinite(row.target_words)?row.target_words:0),0);
 const diff=chapterPlan.target_words-total;
 const valid=list.length===project.scenes.length && list.every(c=>
   Number.isInteger(c.target_words)&&c.target_words>=150&&c.target_words<=12000&&
   c.editorial_goal.length>=1&&c.editorial_goal.length<=500);
 $('#chapterPlanSummary').textContent='Цель '+chapterPlan.target_words.toLocaleString('ru-RU')+
   ' слов (≈ '+chapterPlan.pages_estimate+' стр.) · по главам '+total.toLocaleString('ru-RU')+
   ' слов · '+(diff===0?'сумма совпадает':diff>0?'не хватает '+diff+' слов':'превышение '+(-diff)+' слов')+
   (chapterPlan.source==='author'?' · авторский план в SQLite':' · предварительное распределение')+
   (chapterPlan.stale_saved_plan?' · прежний план устарел, пересчитай и сохрани':'');
 const okay=valid&&diff===0;
 $('#saveChapterPlan').disabled=!okay;
 if(!bookJobId)$('#generateFullBook').disabled=!okay;
 return okay;
}
function renderChapterPlan(){
 const list=$('#chapterPlanList');list.replaceChildren();
 if(!chapterPlan)return;
 chapterPlan.chapters.forEach((chapter,i)=>{
   const article=document.createElement('article');article.className='chapter-plan-card';
   article.dataset.planScene=chapter.scene_id;
   const header=document.createElement('div');header.className='chapter-plan-card-head';
   const h=document.createElement('strong');h.textContent=(i+1)+'. '+chapter.title;
   const pages=document.createElement('small');
   pages.textContent=chapter.stage+' · ≈ '+chapter.pages_estimate+' страниц';
   header.append(h,pages);article.append(header);
   const fields=document.createElement('div');fields.className='chapter-plan-fields';
   function field(label,element){
     const wrap=document.createElement('label');wrap.className='chapter-plan-field';
     const name=document.createElement('span');name.textContent=label;
     wrap.append(name,element);return wrap;
   }
   const words=document.createElement('input');words.type='number';
   words.min='150';words.max='12000';words.step='1';
   words.value=chapter.target_words;words.dataset.planWords='';
   const tension=document.createElement('select');tension.dataset.planTension='';
   tensionLabels.forEach((item,k)=>{const option=document.createElement('option');
     option.value=String(k);option.textContent=item;tension.append(option)});
   tension.value=String(chapter.tension);
   const reveal=document.createElement('select');reveal.dataset.planReveal='';
   Object.entries(revealChoices).forEach(([key,label])=>{
     const option=document.createElement('option');option.value=key;option.textContent=label;
     reveal.append(option);
   });reveal.value=chapter.reveal_level;
   const goal=document.createElement('textarea');goal.rows=2;goal.maxLength=500;
   goal.value=chapter.editorial_goal;goal.dataset.planGoal='';
   fields.append(field('Цель, слов (не токенов)',words),
                 field('Напряжение и темп',tension),
                 field('Что читатель может узнать',reveal),
                 field('Задача главы: действия и движение внимания',goal));
   article.append(fields);list.append(article);
   for(const el of [words,tension,reveal,goal]){
     el.addEventListener('input',()=>{
       const count=Number(words.value);
       pages.textContent=chapter.stage+' · ≈ '+(Number.isFinite(count)?(count/275).toFixed(1):'?')+' страниц';
       chapterPlanTotal();
     });
   }
 });
 chapterPlanTotal();
}
function balanceChapterPlan(){
 if(!chapterPlan)return;
 const rows=Array.from(document.querySelectorAll('#chapterPlanList [data-plan-words]'));
 const original=rows.map(e=>Math.max(150,Number(e.value)||150));
 const available=chapterPlan.target_words-rows.length*150;
 const weights=original.map(n=>Math.max(1,n-150));
 const sum=weights.reduce((a,c)=>a+c,0);
 const raw=weights.map(w=>available*w/sum);
 const values=raw.map(n=>Math.floor(n));
 let remainder=available-values.reduce((a,n)=>a+n,0);
 const indexes=raw.map((n,i)=>i).sort((a,b)=>(raw[b]-values[b])-(raw[a]-values[a]));
 for(let j=0;j<remainder;j++)values[indexes[j%indexes.length]]++;
 rows.forEach((el,i)=>{el.value=150+values[i];el.dispatchEvent(new Event('input'))});
}
async function saveChapterPlan(announce=true){
 if(!project||!chapterPlan||!chapterPlanTotal())
   throw Error('Сумма слов по главам должна совпасть с целевым объёмом.');
 const rows=collectChapterPlan();
 const data=await api('/projects/'+project.id+'/book-plan',{
   method:'PUT',body:JSON.stringify({chapters:rows})
 });
 chapterPlan=data;renderChapterPlan();
 if(announce)toast('Режиссура глав сохранена в SQLite. Сам каркас не менялся.');
 return data;
}

async function startFullBook(){
 if(!project||isBusy)return;
 if(!project.scenes.length){toast('Сначала создай и утверди каркас');return}
 try{
   await save();
   if(!chapterPlan)await loadChapterPlan();
   await saveChapterPlan(false);
   const job=await api('/books/generate',{method:'POST',
      body:JSON.stringify({project_id:project.id,model})});
   bookJobId=job.id;
   bookDraftMode=true;
   bookDraftWords=-1;
   bookDraftFetchedAt=0;
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
$('#saveBrief').onclick=async()=>{try{await save();await loadChapterPlan()}catch(e){toast(e.message)}};
$('#deleteProject').onclick=deleteCurrentProject;
$('#saveScenes').onclick=async()=>{try{collectScenes();await save();await loadChapterPlan();await loadStoryMemory()}catch(e){toast(e.message)}};
$('#saveText').onclick=()=>{collectText();save().catch(e=>toast(e.message))};
$('#addScene').onclick=()=>{collectScenes();project.scenes.push({id:crypto.randomUUID().replaceAll('-',''),title:'Новая сцена',active_objects:[],anchor:'',tempo:'Ровный',reader_info:'',hook:'',text:''});renderScenes();renderWriting()};
$('#writingScene').onchange=()=>{collectText();switchWriting()};
$('#buildOutline').onclick=$('#buildOutline2').onclick=()=>ai('outline');
 $('#generateFullBook').onclick=startFullBook;
 $('#bookGenerate').onclick=startFullBook;
 $('#bookOpenChapterPlan').onclick=()=>gotoTab('structure');
 $('#recalculateChapterPlan').onclick=async()=>{await save();await loadChapterPlan(true)};
 $('#memoryReload').onclick=()=>loadStoryMemory(storyMemory?.branch_id||'main');
 $('#memoryReindex').onclick=reindexStorySearch;
 $('#memoryBranch').onchange=e=>loadStoryMemory(e.target.value);
 $('#memoryAddNode').onclick=addMemoryNode;
 $('#memoryAddEdge').onclick=addMemoryEdge;
 $('#memoryAddBranch').onclick=addMemoryBranch;
 $('#balanceChapterPlan').onclick=balanceChapterPlan;
 $('#saveChapterPlan').onclick=async()=>{try{await save();await saveChapterPlan()}catch(e){toast(e.message)}};
 $('#bookResume').onclick=resumeFullBook;
 $('#bookAccept').onclick=acceptFullBook;
 $('#bookViewDraft').onclick=()=>setBookReaderMode(true);
 $('#bookViewSaved').onclick=()=>setBookReaderMode(false);
 $('#bookDraftRefresh').onclick=()=>refreshBookDraft(true);
 $('#resumeFullBook').onclick=resumeFullBook;
 $('#acceptFullBook').onclick=acceptFullBook;
$('#generateScene').onclick=()=>ai('scene');
$('#runAudit').onclick=()=>ai('audit');
$('#applyAI').onclick=async()=>{await applyAI();if(project)loadChapterPlan()};
$('#saveAuditDecision').onclick=saveAuditDecision;
$('#aiGoStructure').onclick=()=>gotoTab('structure');
$('#aiGoWriting').onclick=()=>gotoTab('write');
$('#aiGoBook').onclick=()=>gotoTab('book');
$('#openBookFromAudit').onclick=()=>gotoTab('book');
$('#openBookFromEditor').onclick=saveAndReadBook;
$('#bookEdit').onclick=()=>gotoTab('write');
$('#bookGoEdition').onclick=()=>gotoTab('brief');
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
