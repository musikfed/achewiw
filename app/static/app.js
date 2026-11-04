const $=s=>document.querySelector(s);
let project=null, projects=[], pending=null, model='qwen3:8b', isBusy=false, previousSceneId=null;
let activeGenerationJobId=null, refreshGenerationBusy=false;
const escaped=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,options={}){const r=await fetch('/api'+path,{headers:{'Content-Type':'application/json'},...options});if(!r.ok){let detail='Ошибка '+r.status;try{detail=(await r.json()).detail||detail}catch{}throw Error(detail)}if(r.status===204)return null;return r.json()}
function toast(message){const t=$('#toast');t.textContent=message;t.style.display='block';clearTimeout(toast.timer);toast.timer=setTimeout(()=>t.style.display='none',5000)}
function setBusy(value){isBusy=value;document.querySelectorAll('button').forEach(b=>{if(['generateScene','buildOutline','buildOutline2','runAudit','unloadModel'].includes(b.id))b.disabled=value});$('#status').textContent=value?'Идёт генерация StoryEngine · сохраняется на сервере':'Студия готова'}
function renderProjects(){$('#projects').innerHTML=projects.map(p=>'<button class="projectItem '+(project?.id===p.id?'selected':'')+'" data-id="'+escaped(p.id)+'"><strong>'+escaped(p.title)+'</strong><small>'+escaped(p.genre)+'</small></button>').join('');document.querySelectorAll('.projectItem').forEach(b=>b.onclick=()=>loadProject(b.dataset.id))}
async function refreshProjects(){projects=await api('/projects');renderProjects()}
function render(){const active=!!project;$('#editor').hidden=!active;$('#empty').hidden=active;$('#heading').textContent=active?project.title:'Ваши истории начинаются здесь.';if(!active)return;document.querySelectorAll('[data-field]').forEach(el=>el.value=project[el.dataset.field]??'');$('#export').href='/api/projects/'+project.id+'/export';$('#meta').textContent=project.scenes.length+' сцен';renderProjects();renderScenes();renderWriting()}
function updateBrief(){document.querySelectorAll('[data-field]').forEach(el=>{project[el.dataset.field]=el.value})}
async function save(){if(!project)return;updateBrief();project=await api('/projects/'+project.id,{method:'PUT',body:JSON.stringify(project)});await refreshProjects();$('#saveState').textContent='Сохранено · '+new Date().toLocaleTimeString();render();toast('Проект сохранён')}
async function loadProject(id){if(isBusy)return;project=await api('/projects/'+id);try{localStorage.setItem('achewiw.activeProjectId',id)}catch{}pending=null;render();await restoreLatestGeneration()}
async function createProject(){if(isBusy)return;const title=prompt('Название произведения','Новая история');if(title===null||!title.trim())return;project=await api('/projects',{method:'POST',body:JSON.stringify({title:title.trim()})});pending=null;activeGenerationJobId=null;$('#aiPanel').hidden=true;await refreshProjects();render();showTab('brief')}
function showTab(name){document.querySelectorAll('.tabpane').forEach(n=>n.hidden=n.id!==name);document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.tab===name));if(name==='audit')renderStats()}
function renderScenes(){$('#sceneCards').innerHTML=project.scenes.map((s,i)=>'<article class="scene" data-index="'+i+'"><div class="sceneTop"><strong>Сцена '+(i+1)+'</strong><button class="removeScene" data-index="'+i+'">Удалить</button></div><div class="sceneFields">'+[['title','Название'],['active_objects','Активные объекты (через запятую)'],['anchor','Якорь-напоминание'],['tempo','Темп'],['reader_info','Информация / подсказки'],['hook','Крючок в конце']].map(([field,label])=>'<label>'+label+(field==='reader_info'||field==='hook'?'<textarea data-scene-field="'+field+'" rows="2">'+escaped(s[field])+'</textarea>':'<input data-scene-field="'+field+'" value="'+escaped(Array.isArray(s[field])?s[field].join(', '):s[field])+'">')+'</label>').join('')+'</div></article>').join('');document.querySelectorAll('.removeScene').forEach(b=>b.onclick=()=>{collectScenes();if(confirm('Удалить сцену?')){project.scenes.splice(Number(b.dataset.index),1);renderScenes();renderWriting()}})}
function collectScenes(){document.querySelectorAll('.scene').forEach(card=>{const s=project.scenes[Number(card.dataset.index)];card.querySelectorAll('[data-scene-field]').forEach(el=>{s[el.dataset.sceneField]=el.dataset.sceneField==='active_objects'?el.value.split(',').map(x=>x.trim()).filter(Boolean):el.value})})}
function renderWriting(){const select=$('#writingScene'),old=select.value;select.innerHTML=project.scenes.map(s=>'<option value="'+escaped(s.id)+'">'+escaped(s.title)+'</option>').join('');if(project.scenes.some(s=>s.id===old))select.value=old;switchWriting()}
function switchWriting(){const s=project.scenes.find(s=>s.id===$('#writingScene').value);$('#storyText').value=s?.text??'';previousSceneId=s?.id??null}
function collectText(){const s=project.scenes.find(s=>s.id===previousSceneId);if(s)s.text=$('#storyText').value}
async function renderStats(){if(!project)return;try{const data=await api('/projects/'+project.id+'/stats');$('#stats').innerHTML='<h3>Всего: '+data.total_words+' слов · ≈ '+data.minutes_read_aloud+' мин вслух (150 слов/мин)</h3>'+data.scenes.map(s=>'<div class="stat"><strong>'+escaped(s.title)+'</strong><span>'+s.words+' слов · '+s.minutes_read_aloud+' мин · <span class="'+(s.overloaded?'warning':'')+'">'+s.active_objects+' сущностей '+(s.overloaded?'⚠ перегруз':'✓')+'</span></span></div>').join('')}catch(e){toast(e.message)}}
function renderGeneration(job){
  if(!project || job.project_id!==project.id)return;
  const ongoing=['queued','running'].includes(job.status);
  setBusy(ongoing);
  $('#aiPanel').hidden=false;
  $('#aiStage').textContent=job.stage||'Ожидание генерации';
  $('#aiJobId').textContent='Задание '+job.id.slice(0,12)+' · '+job.model+' · '+new Date(job.updated_at).toLocaleTimeString();
  $('#aiOutput').textContent=job.result||'Ожидание первого фрагмента текста от Ollama…';
  const progress=$('#aiProgress');
  if(Number.isFinite(job.percent))progress.value=job.percent;
  else progress.removeAttribute('value');
  $('#aiPercent').textContent=job.percent==null?'Ожидание токенов':job.percent+'% до лимита';
  const secs=job.eta_seconds;
  $('#aiEta').textContent=ongoing?
    (secs!=null?'Ориентир до лимита ответа: ~'+Math.max(1,Math.ceil(secs/60))+' мин. ':'Время пока неизвестно. ')+
    'Процент и ETA приблизительные; модель может закончить значительно раньше.':
    'Получено примерно '+(job.estimated_tokens||0)+' токенов. История хранится в SQLite.';
  $('#aiNotice').textContent=ongoing?
    'Ответ поступает частями и сохраняется на сервере. Можно обновить страницу — запись не пропадёт.':
    job.status==='ready'?
      (job.parse_error?'Ответ готов, но JSON-каркас не разобран: '+job.parse_error:
        'Черновик готов. Исходный проект не изменён до принятия предложения.'):
      'Генерация '+(job.status==='interrupted'?'прервана':'завершилась ошибкой')+
        '. Полученный текст сохранён. '+(job.error||'');
  pending=job.status==='ready'?{
    mode:job.mode,model:job.model,result:job.result,
    suggested_scenes:job.suggested_scenes,sceneId:job.scene_id
  }:null;
  $('#applyAI').hidden=!(pending&&(pending.mode==='scene'||(pending.mode==='outline'&&pending.suggested_scenes)));
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
async function ai(mode){
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
  $('#applyAI').hidden=true;pending=null;
  try{
    const job=await api('/ai',{method:'POST',body:JSON.stringify({
      project_id:project.id,mode,model,scene_id:$('#writingScene').value||null
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
function applyAI(){if(!pending)return;if(pending.mode==='outline'&&pending.suggested_scenes){if(project.scenes.length&&!confirm('Заменить текущий каркас предложенным?'))return;project.scenes=pending.suggested_scenes;renderScenes();renderWriting();showTab('structure')}else if(pending.mode==='scene'){const s=project.scenes.find(s=>s.id===pending.sceneId);if(!s)return;if(s.text&&!confirm('Заменить существующий текст сцены?'))return;s.text=pending.result;renderWriting();showTab('write')}$('#aiPanel').hidden=true;toast('Предложение принято. Нажмите «Сохранить» для записи в базу.')}
async function detectModels(){try{const data=await api('/models');const select=$('#model');select.innerHTML='';for(const m of data.models){const o=document.createElement('option');o.value=m;o.textContent=m;select.append(o)}if(data.models.includes(model))select.value=model;else if(data.models.length)model=select.value;$('#unloadModel').disabled=!data.models.length||isBusy;$('#ollamaStatus').textContent='● Ollama на хосте · '+data.models.length+' моделей';$('#ollamaStatus').style.color='#34865b'}catch(e){$('#ollamaStatus').textContent='⚠ '+e.message;$('#ollamaStatus').style.color='#bd6651'}}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{if(!project)return;updateBrief();collectScenes();collectText();showTab(b.dataset.tab)});
$('#newProject').onclick=$('#startProject').onclick=createProject;
$('#saveBrief').onclick=()=>save().catch(e=>toast(e.message));
$('#saveScenes').onclick=()=>{collectScenes();save().catch(e=>toast(e.message))};
$('#saveText').onclick=()=>{collectText();save().catch(e=>toast(e.message))};
$('#addScene').onclick=()=>{collectScenes();project.scenes.push({id:crypto.randomUUID().replaceAll('-',''),title:'Новая сцена',active_objects:[],anchor:'',tempo:'Ровный',reader_info:'',hook:'',text:''});renderScenes();renderWriting()};
$('#writingScene').onchange=()=>{collectText();switchWriting()};
$('#buildOutline').onclick=$('#buildOutline2').onclick=()=>ai('outline');
$('#generateScene').onclick=()=>ai('scene');
$('#runAudit').onclick=()=>ai('audit');
$('#applyAI').onclick=applyAI;
$('#closeAI').onclick=()=>$('#aiPanel').hidden=true;
$('#copyAI').onclick=()=>navigator.clipboard.writeText($('#aiOutput').textContent).then(()=>toast('Скопировано')).catch(()=>toast('Не удалось скопировать'));
$('#model').onchange=e=>model=e.target.value;
$('#unloadModel').onclick=releaseSelectedModel;
setInterval(()=>{if(!document.hidden)refreshGeneration()},1500);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshGeneration()});
(async()=>{try{await refreshProjects();$('#status').textContent='Студия готова';if(projects.length){let previous=null;try{previous=localStorage.getItem('achewiw.activeProjectId')}catch{}await loadProject(projects.find(p=>p.id===previous)?.id||projects[0].id)}detectModels()}catch(e){$('#status').textContent='Ошибка соединения';toast(e.message)}})();
