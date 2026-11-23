/* ACHEWIW R3.7B — deterministic geometric audit of MANUALLY drawn diagrams.
   No OCR, VLM or asserted semantic connectivity. Browser and Node compatible. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object' && module.exports)module.exports=api;
  else root.ACHEWIWDiagramGraph=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const NODE_TYPES=new Set(['rectangle','ellipse']);
  const EDGE_TYPES=new Set(['line','arrow']);
  const finite=n=>typeof n==='number'&&Number.isFinite(n);
  function contains(shape,point){
    const x1=Math.min(shape.x,shape.x+shape.w),x2=Math.max(shape.x,shape.x+shape.w);
    const y1=Math.min(shape.y,shape.y+shape.h),y2=Math.max(shape.y,shape.y+shape.h);
    // Avoid linking an arrow placed near a node by guessed proximity.
    if(shape.type==='rectangle')return point.x>=x1&&point.x<=x2&&point.y>=y1&&point.y<=y2;
    const rx=(x2-x1)/2,ry=(y2-y1)/2;
    if(rx<=0||ry<=0)return false;
    const dx=(point.x-(x1+x2)/2)/rx,dy=(point.y-(y1+y2)/2)/ry;
    return dx*dx+dy*dy<=1;
  }
  function inspect(shapes){
    if(!Array.isArray(shapes)||shapes.length>100)throw Error('Некорректный набор фигур');
    const seen=new Set();
    for(const s of shapes){
      if(!s||typeof s.id!=='string'||seen.has(s.id)||
          !['rectangle','ellipse','line','arrow','cover','text'].includes(s.type)||
          ![s.x,s.y,s.w,s.h].every(finite))
        throw Error('Недопустимая фигура в схеме');
      seen.add(s.id);
    }
    const nodes=shapes.filter(s=>NODE_TYPES.has(s.type));
    const drawnEdges=shapes.filter(s=>EDGE_TYPES.has(s.type));
    const labels=shapes.filter(s=>s.type==='text').map(s=>({
      id:s.id,text:s.text||'',assignment:'not-reviewed'
    }));
    const edges=drawnEdges.map(s=>{
      const source={x:s.x,y:s.y};
      const target={x:s.x+s.w,y:s.y+s.h};
      const find=p=>nodes.filter(n=>contains(n,p)).map(n=>n.id);
      const from=find(source),to=find(target);
      const status=from.length===1&&to.length===1&&from[0]!==to[0]
        ?'candidate':'unresolved';
      const issues=[];
      if(from.length!==1)issues.push(from.length?'start-ambiguous':'start-outside');
      if(to.length!==1)issues.push(to.length?'end-ambiguous':'end-outside');
      if(from.length===1&&to.length===1&&from[0]===to[0])issues.push('same-node');
      return {id:s.id,type:s.type,fromCandidates:from,toCandidates:to,
        status,issues,confirmed:false};
    });
    return {format:'achewiw-diagram-geometry-audit',version:1,
      origin:'manual-geometry',autoRecognized:false,confirmedRelations:0,
      nodes:nodes.map(s=>({id:s.id,type:s.type})),edges,labels,
      summary:{nodes:nodes.length,edges:edges.length,
        candidateEdges:edges.filter(e=>e.status==='candidate').length,
        unresolvedEdges:edges.filter(e=>e.status==='unresolved').length,
        unassignedLabels:labels.length}};
  }
  function applyReviews(audit,decisions){
    if(!audit||!Array.isArray(audit.edges)||!decisions||typeof decisions!=='object')
      throw Error('Некорректные данные рецензирования');
    let confirmed=0,rejected=0;
    const edges=audit.edges.map(edge=>{
      const review=Object.prototype.hasOwnProperty.call(decisions,edge.id)
        ?decisions[edge.id]:null;
      // An ambiguous edge cannot be accepted, even if stale browser data says so.
      const decision=edge.status==='candidate'&&
          (review==='accepted'||review==='rejected')?review:'pending';
      if(decision==='accepted')confirmed++;
      if(decision==='rejected')rejected++;
      return {...edge,reviewDecision:decision,confirmed:decision==='accepted'};
    });
    return {...audit,edges,confirmedRelations:confirmed,
      reviewSummary:{confirmed,rejected,pending:edges.length-confirmed-rejected}};
  }
  function applyLabelReviews(audit,assignments){
    if(!audit||!Array.isArray(audit.nodes)||!Array.isArray(audit.labels)||
        !assignments||typeof assignments!=='object'||Array.isArray(assignments))
      throw Error('Неверные назначения подписей');
    const nodes=new Set(audit.nodes.map(node=>node.id));
    let assigned=0;
    const labels=audit.labels.map(label=>{
      const target=Object.prototype.hasOwnProperty.call(assignments,label.id)
        ?assignments[label.id]:null;
      const valid=typeof target==='string'&&nodes.has(target);
      if(valid)assigned++;
      return {...label,nodeId:valid?target:null,
        assignment:valid?'manual-confirmed':'not-reviewed'};
    });
    return {...audit,labels,confirmedLabels:assigned,
      labelReviewSummary:{assigned,pending:labels.length-assigned}};
  }
  return {inspect,applyReviews,applyLabelReviews};
});
