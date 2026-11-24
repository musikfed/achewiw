(()=>{"use strict";
const $=id=>document.getElementById(id);
let branches=[],points=[],projects=[],editions=[],busy=false;
const selectedProject=()=>$('project').value;
const selectedBranch=()=>branches.find(b=>b.id===$('branch').value);
const base=()=>'/api/projects/'+encodeURIComponent(selectedProject())+'/publication';
const report=(msg,error=false)=>{const n=$('status');n.textContent=msg;n.dataset.error=String(error)};
async function api(path,opts={}){
  const response=await fetch(path,{cache:'no-store',...opts});
  const value=await response.json().catch(()=>({}));
  if(!response.ok)throw Error(value.detail||'HTTP '+response.status);
  return value;
}
async function write(path,value){
  return api(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(value)});
}
function node(tag,content,className){
  const el=document.createElement(tag);
  if(content!==undefined)el.textContent=String(content);
  if(className)el.className=className;
  return el;
}
function action(text,fn){
  const b=node('button',text);b.type='button';
  b.addEventListener('click',async()=>{if(busy)return;busy=true;b.disabled=true;
    try{await fn();report('Операция выполнена. Оригинальная книга не изменена.')}
    catch(err){report(err.message,true)}
    finally{busy=false;b.disabled=false}
  });return b;
}
function resetBranchArea(){
  points=[];$('checkpoints').replaceChildren();$('pointPreview').textContent='';
  $('pointInfo').textContent='Выберите контрольную точку.';
}
async function loadProjects(){
  projects=await api('/api/projects');
  $('project').replaceChildren(node('option','Выберите произведение'));
  $('project').firstChild.value='';
  for(const p of projects){const option=node('option',p.title+' · '+p.work_type);
    option.value=p.id;$('project').append(option)}
}
async function loadBranches(preferred){
  branches=await api(base()+'/branches');
  $('branch').replaceChildren(node('option','Выберите ветку'));
  $('branch').firstChild.value='';
  for(const b of branches){const o=node('option',b.name+' · '+b.medium);o.value=b.id;$('branch').append(o)}
  if(preferred&&branches.some(b=>b.id===preferred))$('branch').value=preferred;
  resetBranchArea();
  await loadEditions();
  if($('branch').value)await loadPoints();
}
async function loadEditions(){
  editions=await api(base()+'/editions');
  const root=$('editions');root.replaceChildren();
  if(!editions.length){root.append(node('p','Пока изданий нет.','muted'));return}
  for(const edition of editions){
    const div=node('div',undefined,'entry');
    div.append(node('strong','#'+edition.edition_number+' · '+edition.label));
    div.append(node('small',edition.medium+' · редакция '+edition.checkpoint_id.slice(0,8)+' · '+edition.created_at));
    div.append(action('Посмотреть редакцию издания',()=>showPoint(edition.checkpoint_id)));
    root.append(div)
  }
}
async function showPoint(id){
  const cp=await api(base()+'/checkpoints/'+encodeURIComponent(id));
  const project=cp.project||{};
  const scenes=Array.isArray(project.scenes)?project.scenes:[];
  $('pointInfo').textContent=(cp.label||'Редакция')+' · '+cp.created_at+
    ' · '+scenes.length+' сцен · контрольная сумма '+cp.project_sha256.slice(0,16);
  $('pointPreview').textContent=JSON.stringify({
    title:project.title,genre:project.genre,
    scenes:scenes.map(s=>({title:s.title,text:(s.text||'').slice(0,360),
                        excerpt_only:(s.text||'').length>360})),
    manifest:cp.manifest
  },null,2);
}
async function loadPoints(){
  const b=selectedBranch();resetBranchArea();if(!b)return;
  points=await api(base()+'/branches/'+b.id+'/checkpoints');
  const root=$('checkpoints');
  for(const cp of points){
    const div=node('div',undefined,'entry');const controls=node('div',undefined,'actions');
    div.append(node('strong',cp.label+' · '+cp.id.slice(0,8)));
    div.append(node('small',cp.created_at));
    controls.append(action('Просмотреть',()=>showPoint(cp.id)));
    controls.append(action('Создать ветку отсюда',async()=>{
      const name=window.prompt('Название новой ветки:',cp.label+' — альтернативная редакция');
      if(!name)return;
      const created=await write(base()+'/branches',{
        name:name.trim(),medium:b.medium,from_checkpoint:cp.id});
      await loadBranches(created.id);
    }));
    controls.append(action('Зафиксировать издание',async()=>{
      const name=window.prompt('Название издания:',cp.label);
      if(!name)return;
      const yes=window.confirm('Закрепить выбранную редакцию как неизменяемое издание? Это НЕ публикация и не архив файлов.');
      if(!yes)return;
      await write(base()+'/editions',{
        checkpoint_id:cp.id,label:name.trim(),medium:b.medium
      });await loadEditions();
    }));
    div.append(controls);root.append(div);
  }
}
$('project').addEventListener('change',async()=>{
  try{
    const id=selectedProject();
    $('projectInfo').textContent='';
    if(!id){$('branch').replaceChildren();resetBranchArea();$('editions').replaceChildren();return}
    const pr=projects.find(x=>x.id===id);
    $('projectInfo').textContent=pr.title+' · тип '+pr.work_type+' · '+pr.updated_at;
    await loadBranches();report('Проект загружен: история доступна без изменения книги.');
  }catch(e){report(e.message,true)}
});
$('branch').addEventListener('change',()=>loadPoints().catch(e=>report(e.message,true)));
$('newBranch').addEventListener('submit',async(e)=>{
  e.preventDefault();if(!selectedProject()){report('Выбери произведение.',true);return}
  const name=$('branchName').value.trim();if(!name)return;
  try{
    const created=await write(base()+'/branches',{name,medium:$('medium').value});
    $('branchName').value='';await loadBranches(created.id);
    report('Новая ветка и начальная контрольная точка созданы.');
  }catch(err){report(err.message,true)}
});
$('checkpoint').addEventListener('click',async()=>{
  const branch=selectedBranch();if(!branch){report('Выбери ветку.',true);return}
  if(!window.confirm('Сохранить текущую рукопись проекта как НОВУЮ точку выбранной ветки? Предыдущие редакции не изменятся.'))return;
  try{
    const project=await api('/api/projects/'+selectedProject());
    const label=$('checkpointLabel').value.trim()||'Редактура';
    await write(base()+'/branches/'+branch.id+'/checkpoints',{
      label,expected_head:branch.current_checkpoint_id,project_state:project
    });
    await loadBranches(branch.id);report('Снимок сохранён, исходная рукопись не изменена.');
  }catch(e){report(e.message,true)}
});
loadProjects().catch(e=>report(e.message,true));
})();