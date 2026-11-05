/* ACHEWIW production conveyor UI: persistent server job, read-only status polling. */
(()=>{
"use strict";
const $=(id)=>document.getElementById(id);
const labels={
 book:"01 · Генерация глав",mechanical_qa:"02 · Точные повторы и структура",
 literary_qa:"03 · Независимый литературный критик",
 editorial_proposals:"04 · Редакторские предложения",
 illustration:"05 · Обложка и иллюстрации",
 proof:"06 · Пробная верстка PDF и EPUB",
 author_review:"07 · Авторское утверждение",archive:"08 · Релиз и архив"};
const states={queued:"В очереди",running:"Выполняется",waiting_author:"Ожидает автора",
 ready:"Готово",error:"Ошибка",interrupted:"Прервано",cancelled:"Отменено"};
let attachedProject="",job=null,loading=false,modelsLoaded=false,reviewLoaded="";
const sleep=(ms)=>new Promise(resolve=>setTimeout(resolve,ms));
async function request(path,options={}){
 const r=await fetch("/api"+path,{headers:{"Content-Type":"application/json"},cache:"no-store",...options});
 if(!r.ok){
  let error="HTTP "+r.status;
  try {const b=await r.json();error=b.detail||error}catch{}
  throw Error(typeof error==="string"?error:JSON.stringify(error))
 }
 return r.status===204?null:r.json()
}
function selectedProject(){
 try{return typeof project!=="undefined"&&project?.id?project.id:null}
 catch{return null}
}
function uiError(e){$("cvStatus").textContent="Ошибка: "+String(e?.message||e)}
function setControls(){
 if(!job){$("cvResume").hidden=$("cvCancel").hidden=true;return}
 $("cvResume").hidden=!["error","interrupted","waiting_author"].includes(job.state)
   ||job.current_step===6;
 $("cvCancel").hidden=!["queued","running","waiting_author","error","interrupted"].includes(job.state);
 $("cvStart").disabled=!!job&&job.state!=="ready"&&job.state!=="cancelled";
}
async function loadModels(){
 if(modelsLoaded)return;
 const result=await request("/models");
 const names=Array.isArray(result.models)?result.models:[];
 for(const id of ["cvWriterModel","cvCriticModel"]){
  const select=$(id);select.replaceChildren();
  for(const name of names){
   const op=document.createElement("option");op.value=name;op.textContent=name;select.append(op)
  }
 }
 const choose=(id,regexes)=>{
  const el=$(id);
  for(const regex of regexes){const v=names.find(x=>regex.test(x));if(v){el.value=v;break}}
 }
 choose("cvWriterModel",[/qwen3\.5:9b/i,/qwen3:8b/i,/hermes.*/i]);
 choose("cvCriticModel",[/Hermes-4-14B/i,/gemma3:12b/i,/qwen3:8b/i]);
 modelsLoaded=true
}
async function load(){
 const id=selectedProject();
 if(!id){job=null;attachedProject="";$("cvStatus").textContent="Выбери книгу";return}
 if(loading)return;
 loading=true;
 try{
  if(attachedProject!==id){job=null;reviewLoaded="";attachedProject=id}
  const jobs=await request("/projects/"+id+"/conveyor");
  job=jobs[0]||null;
  render();
  if(job?.state==="waiting_author"&&job.current_step===6){
   await showReview()
  }
 }catch(e){uiError(e)}finally{loading=false}
}
function el(tag,text,css){
 const n=document.createElement(tag);
 if(text!==undefined)n.textContent=String(text);
 if(css)n.className=css;
 return n
}
function showFile(name,caption){
 const a=el("a",caption);
 a.href="/api/conveyor/jobs/"+job.id+"/files/"+encodeURIComponent(name);
 a.target="_blank";a.rel="noopener noreferrer";a.download=name;
 $("cvFiles").append(a)
}
function render(){
 const status=$("cvStatus"),steps=$("cvSteps"),files=$("cvFiles");
 steps.replaceChildren();files.replaceChildren();
 $("cvApproval").hidden=!job||job.current_step!==6||job.state!=="waiting_author";
 if(!job){status.textContent="Конвейер ещё не запускался";$("cvProgress").value=0;setControls();return}
 status.textContent=(states[job.state]||job.state)+" · "+job.id.slice(0,12)+
  " · "+job.percent+"% шагов"+
  (job.error?" · "+job.error:"");
 $("cvProgress").value=job.percent;
 for(const item of job.steps){
  const row=el("div",null,"cv-step");
  row.append(el("strong",labels[item.stage]||item.stage));
  row.append(el("span"," · "+({done:"Готово",running:"Выполняется",blocked:"Ждёт автора",
    error:"Ошибка",pending:"Ожидает"}[item.status]||item.status)));
  if(item.note)row.append(el("small",item.note));
  steps.append(row)
 }
 const fileNames=new Set(job.artifacts.filter(a=>["proof","archive"].includes(a.stage))
   .map(a=>a.stage));
 if(fileNames.has("proof")){
  showFile("proof-draft-a5.pdf","Проверить черновик PDF A5");
  showFile("proof-draft.epub","Проверить черновик EPUB")
 }
 if(job.state==="ready"&&fileNames.has("archive")){
  for(const [f,label] of [["approved-a5.pdf","Одобренный PDF A5"],
   ["approved.epub","EPUB"],["approved.docx","DOCX"],["approved.fb2","FB2"]])
   showFile(f,label)
 }
 setControls()
}
async function showReview(){
 if(!job||reviewLoaded===job.id)return;
 reviewLoaded="";
 const book=await request("/books/jobs/"+job.book_job_id+"/draft");
 const holder=$("cvReviewItems");holder.replaceChildren();
 const mech=await request("/conveyor/jobs/"+job.id+"/artifacts/mechanical_qa/report")
 .catch(()=>null);
 const count=mech?.body?.finding_count||0;
 holder.append(el("p","Автоматическая проверка нашла "+count+
  " потенциальных дефектов. Часть может требовать ручного анализа."));
 for(const chapter of book.chapters){
  const key=String(chapter.index);
  const proposal=await request("/conveyor/jobs/"+job.id+
      "/artifacts/editorial_proposals/"+key).catch(()=>null);
  const critic=await request("/conveyor/jobs/"+job.id+
      "/artifacts/literary_qa/"+key).catch(()=>null);
  const candidate=proposal?.body?.candidate_text||chapter.text;
  const canEdit=Array.isArray(proposal?.body?.edits)&&proposal.body.edits.length>0&&
                candidate!==chapter.text;
  const details=el("details");
  details.append(el("summary",chapter.index+". "+chapter.title+
     " · "+chapter.words+" слов · "+(canEdit?proposal.body.edits.length+" предложения":"нет точных замен")));
  if(critic?.body?.report){
   const report=el("pre",critic.body.report,"cv-pre");details.append(el("h4","Отчёт критика"),report)
  }
  if(canEdit){
   const check=el("label");check.className="checkbox-row";
   const input=document.createElement("input");input.type="checkbox";
   input.dataset.sceneId=chapter.scene_id;input.className="cv-proposal-choice";
   check.append(input,document.createTextNode("Принять предлагаемую редакцию этой главы"));
   details.append(check)
  }
  const original=el("pre",chapter.text,"cv-pre");
  details.append(el("h4","Исходный черновик"),original);
  if(canEdit){details.append(el("h4","Новая предложенная редакция"),
                           el("pre",candidate,"cv-pre"))}
  holder.append(details)
 }
 reviewLoaded=job.id
}
async function start(){
 const id=selectedProject();if(!id)throw Error("Сначала выбери книгу");
 await loadModels();
 const payload={
  writer_model:$("cvWriterModel").value,critic_model:$("cvCriticModel").value,
  image_mode:$("cvImageMode").value,image_style:$("cvImageStyle").value.trim(),
  allow_shared_unload:$("cvAllowUnload").checked
 };
 if(!payload.writer_model||!payload.critic_model)throw Error("Не найдены модели Ollama");
 job=await request("/projects/"+id+"/conveyor",{
  method:"POST",body:JSON.stringify(payload)});
 render()
}
async function resume(){
 if(!job)return;
 job=await request("/conveyor/jobs/"+job.id+"/resume",{
  method:"POST",body:JSON.stringify({confirm_author_approval:false})});
 render()
}
async function cancel(){
 if(!job)return;
 if(!confirm("Остановить конвейер? Сохранённые главы и результаты не удалятся."))return;
 job=await request("/conveyor/jobs/"+job.id+"/cancel",{method:"POST"});
 render()
}
async function approve(){
 if(!job)return;
 const selected_edits={};
 for(const checkbox of document.querySelectorAll(".cv-proposal-choice")){
  selected_edits[checkbox.dataset.sceneId]=checkbox.checked
 }
 if(!confirm("Подтверждаешь принятие указанных редакций книги в авторскую рукопись? "+
             "Исходный черновик и история правок сохранятся."))return;
 job=await request("/conveyor/jobs/"+job.id+"/approve",{
  method:"POST",body:JSON.stringify({confirm:true,selected_edits})});
 reviewLoaded="";
 render()
}
function bind(id,fn){
 $(id).addEventListener("click",async ()=>{
  try{await fn()}catch(e){uiError(e)}
 })
}
bind("cvStart",start);bind("cvResume",resume);bind("cvRefresh",load);
bind("cvCancel",cancel);bind("cvApprove",approve);
const update=async ()=>{
 const id=selectedProject();
 const section=$("conveyorSection");
 if(id&&section&&!section.closest(".tabpane")?.hidden){
  if(attachedProject!==id||!job||["queued","running","waiting_author"].includes(job.state))
    await load();
  if(!modelsLoaded)loadModels().catch(()=>{});
 }
};
update().catch(()=>{});
setInterval(()=>update().catch(()=>{}),4500);
})();
