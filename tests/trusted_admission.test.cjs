'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const gate=require('../.github/scripts/trusted_admission.cjs');
const APP=2345678;
const HEAD='a'.repeat(40),BASE='b'.repeat(40),CANDIDATE='c'.repeat(40),MERGED='d'.repeat(40);
const body=[
  '## 背景与目标','需要阻断不合规范的变更入口，并保留现有项目质量检查。',
  '## 修改范围与实现','添加独立的提交与 PR 校验入口，准确核对真实提交与最终消息。',
  '## 兼容性与风险','保持现有离线查询行为与数据格式，保护现有词条和来源记录。',
  '## 实际验证','已覆盖合法中文说明、错误提交消息和分支变化的正反例测试。',
  '## 未完成事项','仍需安装独立应用并完成授权配置，当前不执行目标分支合并。'
].join('\n');
function pull(number=36) {
  return {
    number,title:'feat(governance): 增加独立提交与合并消息验证入口',body,
    state:'open',merged:false,draft:false,mergeable:true,
    updated_at:'2026-11-06T00:00:00Z',merge_commit_sha:CANDIDATE,
    commits:1,changed_files:1,
    head:{ref:'feat/remote-admission',sha:HEAD,repo:{id:gate.REPOSITORY_ID}},
    base:{ref:'main',sha:BASE,repo:{id:gate.REPOSITORY_ID,full_name:gate.REPOSITORY}}
  };
}
function fixture(pulls=[pull()]) {
  const state={
    pulls,requests:[],getCount:0,checkId:10,
    files:[{filename:'.github/scripts/governance_gate.cjs'}],
    commits:[{sha:HEAD,commit:{message:'fix(governance): 修正真实合并消息的校验入口\n\n根据真实提交记录检查最终合并消息，并保留原有质量检查与分支保护约束。'}}],
    rulesets:gate.rulesetPlan(APP),nativeChecks:true
  };
  const copy=x=>structuredClone(x);
  const prefix='/repos/'+gate.REPOSITORY;
  const client={
    appId:APP,
    async list(url) {
      state.requests.push(['LIST',url]);
      if(url===prefix+'/pulls?state=open') return copy(state.pulls.filter(p=>p.state==='open'));
      if(url.endsWith('/commits')) return copy(state.commits);
      if(url.endsWith('/files')) return copy(state.files);
      throw new Error('Unexpected list '+url);
    },
    async request(method,url,data) {
      state.requests.push([method,url,copy(data)]);
      if(method==='GET'&&url===prefix) return {id:gate.REPOSITORY_ID,full_name:gate.REPOSITORY};
      const number=/\/pulls\/(\d+)$/u.exec(url);
      if(method==='GET'&&number) {
        state.getCount++;
        state.onGet?.(state.getCount);
        return copy(state.pulls.find(p=>p.number===Number(number[1])));
      }
      if(method==='POST'&&url===prefix+'/check-runs') return {id:state.checkId++,app:{id:state.checkApp||APP}};
      if(method==='PATCH'&&url.startsWith(prefix+'/check-runs/')) return {id:10,...copy(data)};
      if(method==='GET'&&url.startsWith(prefix+'/rules/branches/')) return state.rulesets.map((s,i)=>({type:s.rules[0].type,ruleset_id:i+1}));
      const rule=/\/rulesets\/(\d+)$/u.exec(url);
      if(method==='GET'&&rule) return copy(state.rulesets[Number(rule[1])-1]);
      if(method==='PUT'&&url.endsWith('/merge')) {
        if(!state.nativeChecks) throw new Error('GitHub API 返回 405');
        const n=Number(/\/pulls\/(\d+)\/merge$/u.exec(url)[1]);
        const pr=state.pulls.find(p=>p.number===n);
        assert.equal(data.sha,pr.head.sha);
        assert.equal(data.merge_method,'squash');
        pr.merged=true;pr.state='closed';pr.merge_commit_sha=MERGED;
        state.actualMessage=data.commit_title+'\n\n'+data.commit_message;
        if(state.mergeTimeout) throw new Error('连接超时');
        return {merged:true,sha:MERGED};
      }
      if(method==='GET'&&url===prefix+'/git/commits/'+MERGED)
        return {sha:MERGED,message:state.actualMessage+(state.extraMessage||''),
          parents:[{sha:BASE}]};
      throw new Error('Unexpected request '+method+' '+url);
    }
  };
  return {state,client};
}
function writes(state,method='PUT') {
  return state.requests.filter(([m])=>m===method);
}
test('合法 PR 使用完整提交与差异构造明确的最终 squash 消息',async()=>{
  const {client,state}=fixture();
  const result=await gate.evaluate(client,36);
  assert.deepEqual(result.errors,[]);
  assert.equal(result.message.sha,HEAD);
  assert.equal(result.message.commit_title,state.pulls[0].title);
  assert.equal(result.message.commit_message,body);
  assert.deepEqual(writes(state),[]);
});
test('受审分支替换自己的校验器不能放行违规标题',async()=>{
  const {client,state}=fixture();
  state.pulls[0].title='fix(api): bypass';
  const result=await gate.evaluate(client,36);
  assert(result.errors.some(e=>e.includes('中文')));
  assert(!state.requests.some(([,url])=>/contents|blobs|trees/u.test(url)));
  assert.deepEqual(writes(state),[]);
});
test('最终消息单独验证不兼容说明，PR 标题通过不能代替',async()=>{
  const {client,state}=fixture();
  state.pulls[0].title='feat(governance)!: 调整独立提交与合并消息验证入口';
  const result=await gate.evaluate(client,36);
  assert(result.errors.some(e=>e.includes('最终 squash')&&e.includes('BREAKING CHANGE')));
});
test('同一 HEAD 的元数据变化和目标基线变化均拒绝旧结果',async()=>{
  for(const mutate of [
    p=>{p.body=body.replace('## 实际验证','## 运行情况');},
    p=>{p.base.sha='e'.repeat(40);},
    p=>{p.head.sha='e'.repeat(40);},
    p=>{p.base.ref='release/v0.1.5-queue-item-8';}
  ]) {
    const {client,state}=fixture();
    state.onGet=count=>{if(count===2) mutate(state.pulls[0]);};
    const result=await gate.evaluate(client,36);
    assert(result.errors.some(e=>e.includes('校验期间变化')));
  }
});
test('真实列表缺项、超出 API 提交范围或错误仓库身份均失败关闭',async()=>{
  for(const mutate of [
    s=>{s.pulls[0].commits=2;},
    s=>{s.pulls[0].changed_files=2;},
    s=>{s.pulls[0].commits=251;},
    s=>{s.pulls[0].base.repo.id=1;},
    s=>{s.pulls[0].merge_commit_sha=null;},
    s=>{s.pulls[0].base.ref='other/unprotected';}
  ]) {
    const {client,state}=fixture();
    mutate(state);
    await assert.rejects(gate.evaluate(client,36));
    assert.deepEqual(writes(state),[]);
  }
});
test('草稿、冲突及真实提交中的不可见字符不会得到通过结果',async()=>{
  for(const mutate of [
    s=>{s.pulls[0].draft=true;},
    s=>{s.pulls[0].mergeable=false;},
    s=>{s.commits[0].commit.message+='\n\u202E';}
  ]) {
    const {client,state}=fixture();
    mutate(state);
    assert((await gate.evaluate(client,36)).errors.length);
  }
});
test('同一 SHA 的多个 PR 聚合失败，不能用另一目标分支的成功覆盖',async()=>{
  const first=pull(36),second=pull(45);
  second.base.ref='feat/governance-pr-metadata-gate';
  second.body='缺少必需章节';
  const {client,state}=fixture([first,second]);
  const groups=await gate.openGroups(client);
  const result=await gate.publishGroup(client,HEAD,groups.get(HEAD));
  assert(result.errors.some(e=>e.includes('PR #45')));
  const update=state.requests.find(([method])=>method==='PATCH')[2];
  assert.equal(update.conclusion,'failure');
  assert.equal(state.requests.find(([method])=>method==='POST')[2].status,'in_progress');
});
test('检查写入来源必须是配置的独立 App',async()=>{
  const {client,state}=fixture();
  state.checkApp=15368;
  await assert.rejects(gate.publishGroup(client,HEAD,state.pulls),/检查来源/u);
  assert(!state.requests.some(([method])=>method==='PATCH'));
});
test('队列、重建来源修复及治理目标保护保留三个门禁和零宽泛绕过',()=>{
  const [baseline,update]=gate.rulesetPlan(APP);
  for(const branch of ['main','release/v0.1.5-queue-item-16-early-nothing-to-fear',
    'fix/v0.1.5-duplicate-source-reference','fix/v0.1.5-duplicate-source-reference-rebuild',
    'feat/governance-pr-metadata-gate']) assert(gate.supported(branch),branch);
  assert(!gate.supported('release/v0.1.5-queue-batch-7'));
  assert(!gate.supported('feature/unreviewed'));
  assert(!gate.supported('release/v0.1.5-queue-item-1/unprotected'));
  assert(gate.validBaseline(baseline,APP));
  assert(gate.validUpdateGate(update,APP));
  assert.deepEqual(baseline.bypass_actors,[]);
  assert.throws(()=>gate.rulesetPlan(15368));
  for(const id of [0,-1,1.5,NaN]) assert.throws(()=>gate.rulesetPlan(id));
});
test('独立保护目标与三项实际工作流的目标范围一致',()=>{
  const directory=path.join(__dirname,'../.github/workflows');
  for(const name of fs.readdirSync(directory)) {
    const workflow=fs.readFileSync(path.join(directory,name),'utf8');
    const branches=JSON.parse(/branches: (\[[^\n]+\])/u.exec(workflow)[1]);
    assert.deepEqual(branches.map(branch=>'refs/heads/'+branch),gate.TARGETS,name);
  }
});
test('质量规则可绕过、检查来源错误和人工更新入口均阻止受控合并',async()=>{
  for(const mutate of [
    s=>{delete s.rulesets[0].bypass_actors;},
    s=>{s.rulesets[0].bypass_actors=[{actor_type:'Integration',actor_id:APP,bypass_mode:'always'}];},
    s=>{s.rulesets[0].rules.find(r=>r.type==='required_status_checks').parameters.required_status_checks[3].integration_id=15368;},
    s=>{s.rulesets[0].rules.find(r=>r.type==='pull_request').parameters.required_review_thread_resolution=false;},
    s=>{s.rulesets[1].bypass_actors.push({actor_type:'RepositoryRole',actor_id:5,bypass_mode:'always'});},
    s=>{s.rulesets[1].rules[0].parameters.update_allows_fetch_and_merge=true;}
  ]) {
    const {client,state}=fixture();mutate(state);
    await assert.rejects(gate.merge(client,36),/分支缺少/u);
    assert.deepEqual(writes(state),[]);
  }
});
test('合并只发送已验证的原文及固定 HEAD，随后核对真实提交对象',async()=>{
  const {client,state}=fixture();
  assert.deepEqual(await gate.merge(client,36),{sha:MERGED,merged:true});
  const requests=writes(state);
  assert.equal(requests.length,1);
  assert.deepEqual(requests[0][2],{
    commit_title:pull().title,commit_message:body,sha:HEAD,merge_method:'squash'
  });
  assert(state.requests.some(([,url])=>url.endsWith('/git/commits/'+MERGED)));
});
test('最后复核时发生变化或原生必需检查拒绝，不重试合并',async()=>{
  const first=fixture();
  first.state.onGet=count=>{if(count===3) first.state.pulls[0].title='fix(api): 不完整';};
  await assert.rejects(gate.merge(first.client,36),/状态已变化/u);
  assert.equal(writes(first.state).length,0);
  const second=fixture();second.state.nativeChecks=false;
  await assert.rejects(gate.merge(second.client,36),/禁止盲目重试/u);
  assert.equal(writes(second.state).length,1);
});
test('合并响应丢失后只读回确认，真实消息不符必须报告失败',async()=>{
  const first=fixture();first.state.mergeTimeout=true;
  assert.deepEqual(await gate.merge(first.client,36),{sha:MERGED,merged:true});
  assert.equal(writes(first.state).length,1);
  const second=fixture();second.state.extraMessage='\n\u202E';
  await assert.rejects(gate.merge(second.client,36),/实际 squash 提交/u);
  assert.equal(writes(second.state).length,1);
});
test('HTTP 客户端固定官方域名、禁重定向且写入不自动重试',async()=>{
  const requests=[];
  const client=new gate.GitHub(async()=>'synthetic-secret',{fetchImpl:async(url,options)=>{
    requests.push({url,options});
    return new Response('{"message":"synthetic"}',{status:403});
  }});
  await assert.rejects(client.request('PUT','/repos/ranzevor/BilibiliMemeAtlas/pulls/36/merge',{}),error=>{
    assert(!error.message.includes('synthetic-secret'));
    return /403/u.test(error.message);
  });
  assert.equal(requests.length,1);
  assert.equal(requests[0].url,'https://api.github.com/repos/ranzevor/BilibiliMemeAtlas/pulls/36/merge');
  assert.equal(requests[0].options.redirect,'error');
  await assert.rejects(client.request('GET','//external.example'));
});
test('分页不得截断完整百项列表',async()=>{
  const pages=[];
  const client=new gate.GitHub(async()=>'synthetic',{fetchImpl:async(url)=>{
    pages.push(url);
    return new Response(JSON.stringify(pages.length===1?Array.from({length:100},(_,id)=>({id})):[{id:100}]));
  }});
  assert.equal((await client.list('/repos/ranzevor/BilibiliMemeAtlas/pulls?state=open')).length,101);
  assert.equal(pages.length,2);
  assert(pages[1].endsWith('&per_page=100&page=2'));
});
test('持续校验缓存确定性失败，原 HEAD 编辑后仍重新检查',async()=>{
  const {client,state}=fixture();
  state.pulls[0].body='缺少必需章节';
  let cycles=0;
  await assert.rejects(gate.watch(client,{sleep:async()=>{
    cycles++;
    if(cycles===2) state.pulls[0].body=body;
    if(cycles===3) throw new Error('stop synthetic loop');
  }}),/stop synthetic loop/u);
  assert.equal(writes(state,'POST').length,2);
  assert.deepEqual(writes(state,'PATCH').map(r=>r[2].conclusion),['failure','success']);
  assert.equal(writes(state).length,0);
});
test('独立 App 的 RSA 身份和安装令牌限定到本仓库与最小权限',async()=>{
  const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'bma-app-auth-'));
  try {
    const keys=crypto.generateKeyPairSync('rsa',{modulusLength:2048});
    const file=path.join(temporary,'private.pem');
    fs.writeFileSync(file,keys.privateKey.export({type:'pkcs8',format:'pem'}));
    const requests=[];
    const fetchImpl=async(url,options)=>{
      requests.push({url,options});
      if(url==='https://api.github.com/app') {
        const jwt=options.headers.Authorization.slice('Bearer '.length).split('.');
        assert(crypto.verify('RSA-SHA256',Buffer.from(jwt.slice(0,2).join('.')),
          keys.publicKey,Buffer.from(jwt[2],'base64url')));
        assert.equal(JSON.parse(Buffer.from(jwt[1],'base64url')).iss,APP);
        return new Response(JSON.stringify({id:APP}));
      }
      if(url.endsWith('/app/installations/1')) return new Response(JSON.stringify({app_id:APP,account:{login:'ranzevor'}}));
      if(url.endsWith('/access_tokens')) return new Response(JSON.stringify({
        token:'synthetic-installation-token',expires_at:new Date(Date.now()+3600000).toISOString()
      }));
      assert.equal(options.headers.Authorization,'Bearer synthetic-installation-token');
      return new Response(JSON.stringify({id:gate.REPOSITORY_ID}));
    };
    const client=await gate.installationClient({
      GOVERNANCE_APP_ID:String(APP),GOVERNANCE_INSTALLATION_ID:'1',GOVERNANCE_PRIVATE_KEY_FILE:file
    },fetchImpl);
    await client.request('GET','/repos/ranzevor/BilibiliMemeAtlas');
    const tokenRequest=JSON.parse(requests.find(r=>r.url.endsWith('/access_tokens')).options.body);
    assert.deepEqual(tokenRequest.repository_ids,[gate.REPOSITORY_ID]);
    assert.deepEqual(tokenRequest.permissions,{contents:'write',checks:'write',pull_requests:'read'});
    assert(!('administration' in tokenRequest.permissions));
  } finally {
    assert(path.resolve(temporary).startsWith(path.resolve(os.tmpdir())+path.sep+'bma-app-auth-'));
    fs.rmSync(temporary,{recursive:true,force:true});
  }
});
