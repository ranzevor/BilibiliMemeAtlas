'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const fs=require('node:fs');
const http=require('node:http');
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
    pulls,requests:[],getCount:0,checkId:10,checkRuns:[],listCount:0,
    files:[{filename:'.github/scripts/governance_gate.cjs'}],
    commits:[{sha:HEAD,commit:{message:'fix(governance): 修正真实合并消息的校验入口\n\n根据真实提交记录检查最终合并消息，并保留原有质量检查与分支保护约束。'}}],
    rulesets:gate.rulesetPlan(APP).map((rule,index)=>({...rule,id:index+1,
      source:gate.REPOSITORY,source_type:'Repository'})),nativeChecks:true
  };
  const copy=x=>structuredClone(x);
  const prefix='/repos/'+gate.REPOSITORY;
  const client={
    appId:APP,
    async list(url) {
      state.requests.push(['LIST',url]);
      if(url===prefix+'/pulls?state=open') {
        state.onList?.(++state.listCount);
        const rows=copy(state.pulls.filter(p=>p.state==='open'));
        if(state.realListing)for(const row of rows)for(const key of ['mergeable','merged','commits','changed_files'])delete row[key];
        return rows;
      }
      if(url.endsWith('/commits')) {
        const number=Number(/\/pulls\/(\d+)\/commits/u.exec(url)[1]);
        const head=state.pulls.find(p=>p.number===number).head.sha;
        return copy(state.commitsByHead?.[head]||state.commits);
      }
      if(url.endsWith('/files')) return copy(state.files);
      throw new Error('Unexpected list '+url);
    },
    async request(method,url,data) {
      state.requests.push([method,url,copy(data)]);
      if(method==='GET'&&url===prefix) return {id:gate.REPOSITORY_ID,full_name:gate.REPOSITORY};
      const number=/\/pulls\/(\d+)$/u.exec(url);
      if(method==='GET'&&number) {
        state.getCount++;
        state.onGet?.(state.getCount,Number(number[1]));
        return copy(state.pulls.find(p=>p.number===Number(number[1])));
      }
      if(method==='POST'&&url===prefix+'/check-runs') {
        state.onCreate?.(data);
        state.checkId=Math.max(state.checkId,...state.checkRuns.map(c=>c.id+1));
        const check={...copy(data),id:state.checkId++,app:{id:state.checkApp||APP}};
        state.checkRuns.push(check);return copy(check);
      }
      if(method==='PATCH'&&url.startsWith(prefix+'/check-runs/')) {
        state.onPatch?.(data);
        const check=state.checkRuns.find(c=>c.id===Number(url.split('/').at(-1)));
        Object.assign(check,copy(data));
        state.afterPublish?.();
        return copy(check);
      }
      if(method==='GET'&&url.includes('/check-runs?')) {
        const rows=state.checkRuns.filter(c=>url.includes('/'+c.head_sha+'/'));
        const response={total_count:rows.length,check_runs:copy(rows)};
        return state.checkView?state.checkView(response):response;
      }
      if(method==='GET'&&url.startsWith(prefix+'/rules/branches/')) return state.rulesets.map((s,i)=>({type:s.rules[0].type,ruleset_id:i+1}));
      const rule=/\/rulesets\/(\d+)$/u.exec(url);
      if(method==='GET'&&rule) {
        state.onRule?.(Number(rule[1]));
        const full=copy(state.rulesets[Number(rule[1])-1]);
        return state.ruleView?state.ruleView(full):full;
      }
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
test('真实列表缺项、无效数量或错误仓库身份均失败关闭',async()=>{
  for(const mutate of [
    s=>{s.pulls[0].commits=2;},
    s=>{s.pulls[0].changed_files=2;},
    s=>{s.pulls[0].commits=null;},
    s=>{s.pulls[0].changed_files=0;},
    s=>{s.pulls[0].base.repo.id=1;},
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
test('开放列表与 PR 详情的可观察元数据不一致时拒绝发布成功',async()=>{
  for(const mutate of [
    p=>{p.title='feat(api): bypass';},
    p=>{p.body='缺少必需章节';},
    p=>{p.base.sha='e'.repeat(40);},
    p=>{p.head.ref='feat/another-source';},
    p=>{p.updated_at='2026-11-06T00:00:01Z';}
  ]) {
    const {client,state}=fixture();
    state.realListing=true;
    const list=client.list.bind(client);
    client.list=async url=>{
      const rows=await list(url);
      if(url.endsWith('/pulls?state=open'))mutate(rows[0]);
      return rows;
    };
    const groups=await gate.openGroups(client);
    const result=await gate.publishGroup(client,HEAD,groups.get(HEAD));
    assert(result.errors.some(e=>e.includes('列表与详情')));
    assert.equal(result.retry,true);
    assert.equal(state.checkRuns.at(-1).conclusion,'failure');
    assert.equal(writes(state).length,0);
  }
});
test('同一 HEAD 的另一 PR 列表与详情不一致时阻止最终合并',async()=>{
  const {client,state}=fixture([pull(),pull(45)]);
  state.realListing=true;
  const list=client.list.bind(client);
  client.list=async url=>{
    const rows=await list(url);
    if(url.endsWith('/pulls?state=open'))rows.find(p=>p.number===45).body='缺少必需章节';
    return rows;
  };
  await assert.rejects(gate.merge(client,36),/列表与详情/u);
  assert.equal(state.checkRuns.at(-1).conclusion,'failure');
  assert.equal(writes(state).length,0);
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
function croppedRuleFixture() {
  const f=fixture();
  f.state.rulesets=f.state.rulesets.map((rule,index)=>({...rule,id:index+1,
    source:gate.REPOSITORY,source_type:'Repository',updated_at:'2026-11-06T16:00:00.123+08:00'}));
  delete f.state.rulesets[1].rules[0].parameters;
  f.client.ruleVersions=new Map(f.state.rulesets.map(rule=>[rule.id,structuredClone(rule)]));
  f.state.ruleView=rule=>{
    delete rule.bypass_actors;
    rule.updated_at='2026-11-06T08:00:00.123Z';
    rule.current_user_can_bypass=rule.rules.some(r=>r.type==='update')?'always':'never';
    return rule;
  };
  return f;
}
test('权限裁剪视图须与管理员版本及实际旁路状态一致，时区格式可不同',async()=>{
  const {client,state}=croppedRuleFixture();
  const legacy=structuredClone(state.rulesets[0]);
  legacy.id=3;
  legacy.conditions.ref_name.include=['refs/heads/main'];
  legacy.rules.find(rule=>rule.type==='required_status_checks').parameters.required_status_checks.pop();
  state.rulesets.push(legacy);
  assert.deepEqual(await gate.merge(client,36),{sha:MERGED,merged:true});
  assert(gate.validUpdateGate({...gate.rulesetPlan(APP)[1],rules:[{type:'update'}]},APP));
  assert(!gate.validUpdateGate({...gate.rulesetPlan(APP)[1],
    rules:[{type:'update',parameters:{update_allows_fetch_and_merge:true}}]},APP));
});
test('管理员记录缺失、版本变化、公开规则变化或旁路状态异常均停止合并',async()=>{
  for(const mutate of [
    f=>{f.client.ruleVersions=undefined;},
    f=>{f.client.ruleVersions.get(1).updated_at='2026-11-06T08:00:01.123Z';},
    f=>{f.client.ruleVersions.get(1).conditions.ref_name.exclude=['refs/heads/main'];},
    f=>{const view=f.state.ruleView;f.state.ruleView=rule=>({...view(rule),current_user_can_bypass:'always'});},
    f=>{const view=f.state.ruleView;f.state.ruleView=rule=>({...view(rule),bypass_actors:null});}
  ]) {
    const f=croppedRuleFixture();mutate(f);
    await assert.rejects(gate.merge(f.client,36),/目标分支缺少/u);
    assert.equal(writes(f.state).length,0);
  }
});
test('管理员读取入口只接受完整且与目标范围一致的两项规则',async()=>{
  const full=gate.rulesetPlan(APP).map((rule,index)=>({...rule,id:index+1,
    source:gate.REPOSITORY,source_type:'Repository',updated_at:'2026-11-06T08:00:00.123Z'}));
  const client={
    async list(){return structuredClone(full);},
    async request(method,endpoint) {
      assert.equal(method,'GET');
      if(endpoint.endsWith('/'+gate.REPOSITORY)) return {id:gate.REPOSITORY_ID,full_name:gate.REPOSITORY};
      return structuredClone(full[Number(endpoint.split('/').at(-1))-1]);
    }
  };
  assert.equal((await gate.auditRules(client,APP)).rulesets.length,2);
  delete full[0].bypass_actors;
  await assert.rejects(gate.auditRules(client,APP),/管理员规则视图/u);
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
    const issued={token:'synthetic-installation-token',expires_at:new Date(Date.now()+3600000).toISOString(),
      permissions:{contents:'write',checks:'write',pull_requests:'read',metadata:'read'},
      repositories:[{id:gate.REPOSITORY_ID}]};
    let unauthorized=false;
    const fetchImpl=async(url,options)=>{
      requests.push({url,options});
      if(url==='https://api.github.com/app') {
        const jwt=options.headers.Authorization.slice('Bearer '.length).split('.');
        assert(crypto.verify('RSA-SHA256',Buffer.from(jwt.slice(0,2).join('.')),
          keys.publicKey,Buffer.from(jwt[2],'base64url')));
        assert.equal(JSON.parse(Buffer.from(jwt[1],'base64url')).iss,APP);
        return new Response(JSON.stringify({id:APP}));
      }
      if(url.endsWith('/app/installations/1')) return new Response(JSON.stringify({id:1,app_id:APP,account:{login:'ranzevor'}}));
      if(url.endsWith('/access_tokens')) return new Response(JSON.stringify(issued));
      assert.equal(options.headers.Authorization,'Bearer synthetic-installation-token');
      if(unauthorized){unauthorized=false;return new Response('{}',{status:401});}
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
    unauthorized=true;
    await assert.rejects(client.request('PUT','/repos/ranzevor/BilibiliMemeAtlas/pulls/36/merge',{}),e=>e.status===401);
    assert.equal(requests.filter(r=>r.url.endsWith('/access_tokens')).length,1);
    await client.request('GET','/repos/ranzevor/BilibiliMemeAtlas');
    assert.equal(requests.filter(r=>r.url.endsWith('/access_tokens')).length,2);
    for(const mutate of [
      r=>{r.permissions.administration='write';},r=>{r.permissions.pull_requests='write';},
      r=>{r.repositories=[{id:1}];},r=>{r.expires_at='invalid';},r=>{r.token='';}]) {
      const original=structuredClone(issued);mutate(issued);
      const invalid=await gate.installationClient({GOVERNANCE_APP_ID:String(APP),GOVERNANCE_INSTALLATION_ID:'1',
        GOVERNANCE_PRIVATE_KEY_FILE:file},fetchImpl);
      await assert.rejects(invalid.request('GET','/repos/ranzevor/BilibiliMemeAtlas'),/令牌响应/u);
      await assert.rejects(invalid.request('GET','/repos/ranzevor/BilibiliMemeAtlas'),/令牌响应/u);
      for(const key of Object.keys(issued))delete issued[key];Object.assign(issued,original);
    }
    const wrongRecord=path.join(temporary,'wrong-rules.json');
    fs.writeFileSync(wrongRecord,JSON.stringify({repository:'example/other',repository_id:1,app_id:APP,rulesets:[]}));
    await assert.rejects(gate.installationClient({
      GOVERNANCE_APP_ID:String(APP),GOVERNANCE_INSTALLATION_ID:'1',GOVERNANCE_PRIVATE_KEY_FILE:file,
      GOVERNANCE_RULES_FILE:wrongRecord
    },fetchImpl),/管理员规则版本记录/u);
  } finally {
    assert(path.resolve(temporary).startsWith(path.resolve(os.tmpdir())+path.sep+'bma-app-auth-'));
    fs.rmSync(temporary,{recursive:true,force:true});
  }
});

test('共享 HEAD 的其他 PR 在检查发布后变化时，合并前拒绝旧成功结果',async()=>{
  const mutations=[
    s=>{s.pulls[1].title='fix(api): bypass';},
    s=>{s.pulls[1].body='缺少必需章节';},
    s=>{s.pulls[1].title='fix(governance): 更新另一项合法的提交说明';},
    s=>{s.pulls[1].base.sha='e'.repeat(40);},
    s=>{s.pulls[1].head.sha='e'.repeat(40);},
    s=>{s.pulls[1].base.ref='release/v0.1.5-queue-item-next';},
    s=>{s.pulls[1].base.ref='other/unsupported';},
    s=>{s.pulls[1].state='closed';},
    s=>{s.pulls[1].draft=true;},
    s=>{s.pulls.push(pull(46));},
    s=>{s.pulls[2].state='open';}
  ];
  for(const mutate of mutations){
    const closed=pull(46);closed.state='closed';
    const {state,client}=fixture([pull(36),pull(45),closed]);
    state.afterPublish=()=>{state.afterPublish=undefined;mutate(state);};
    await assert.rejects(gate.merge(client,36),/合并前/u);
    assert.equal(writes(state).length,0);
  }
});
test('最终全组读取期间及规则复核期间发生变化，末次集合检查仍拒绝写入',async()=>{
  for(const stage of ['during-pr','during-rules']){
    const {state,client}=fixture([pull(36),pull(45)]);
    state.onGet=(count,number)=>{
      if(stage==='during-pr'&&count===7&&number===45)state.pulls[0].body='缺少必需章节';
    };
    let rules=0;
    state.onRule=()=>{if(stage==='during-rules'&&++rules===4)state.pulls[1].title='fix(api): bypass';};
    await assert.rejects(gate.merge(client,36),/合并前/u);
    assert.equal(writes(state).length,0);
  }
});
test('实际列表省略详情字段时，合法共享 HEAD 全组仍可通过并只写入一次',async()=>{
  const {state,client}=fixture([pull(36),pull(45)]);state.realListing=true;
  assert.deepEqual(await gate.merge(client,36),{sha:MERGED,merged:true});
  assert.equal(writes(state).length,1);
});
test('独立准入实现不能 squash 进中间分支，main 目标及其他治理修复分别处理',async()=>{
  for(const file of [{filename:'.github/scripts/trusted_admission.cjs'},
    {filename:'.github/scripts/admission.cjs',previous_filename:'.github/scripts/trusted_admission.cjs'}]){
    const {state,client}=fixture();state.files=[file];state.pulls[0].base.ref='feat/governance-pr-metadata-gate';
    await assert.rejects(gate.merge(client,36),/main 为最终合并目标/u);
    assert.equal(writes(state).length,0);
  }
  const main=fixture();main.state.files=[{filename:'.github/scripts/trusted_admission.cjs'}];
  assert.equal((await gate.merge(main.client,36)).merged,true);
  const related=fixture();related.state.pulls[0].base.ref='feat/governance-pr-metadata-gate';
  assert.equal((await gate.merge(related.client,36)).merged,true);
});
test('提交、文件和 PR 分页响应中的重复对象不能掩盖列表缺项',async()=>{
  const commits=fixture();commits.state.pulls[0].commits=2;commits.state.commits.push(structuredClone(commits.state.commits[0]));
  await assert.rejects(gate.evaluate(commits.client,36),/提交列表/u);
  const files=fixture();files.state.pulls[0].changed_files=2;files.state.files.push({...files.state.files[0]});
  await assert.rejects(gate.evaluate(files.client,36),/文件列表/u);
  const pulls=fixture([pull(36),pull(36)]);
  await assert.rejects(gate.openGroups(pulls.client),/重复/u);
  for(const f of [commits,files,pulls])assert.equal(writes(f.state).length,0);
});
test('规则详情必须对应生效规则 ID 和本仓库，不接受错误身份的规则响应',async()=>{
  for(const mutate of [r=>{r.id=999;},r=>{r.source='example/other';},r=>{r.target='tag';}]){
    const f=fixture();f.state.ruleView=r=>{mutate(r);return r;};
    await assert.rejects(gate.merge(f.client,36),/规则|分支缺少/u);
    assert.equal(writes(f.state).length,0);
  }
});
async function cycles(f,steps,{start=Date.parse('2026-11-07T00:00:00Z'),interval=60000}={}){
  let time=start,cycle=0;
  await assert.rejects(gate.watch(f.client,{now:()=>time,sleep:async()=>{
    cycle++;
    if(cycle>=steps.length)throw new Error('stop synthetic loop');
    const advance=steps[cycle-1]?.(f.state,cycle,time);
    time+=advance??interval;
  }}),/stop synthetic loop/u);
  return f;
}
test('mergeable null 与候选未计算完成在无元数据变化恢复后重新检查',async()=>{
  for(const mutate of [p=>{p.mergeable=null;},p=>{p.merge_commit_sha=null;},p=>{p.mergeable=false;}]){
    const f=fixture();mutate(f.state.pulls[0]);
    const result=await gate.evaluate(f.client,36);assert(result.retry);assert(result.errors.length);
    await cycles(f,[s=>{s.pulls[0].mergeable=true;s.pulls[0].merge_commit_sha=CANDIDATE;},null]);
    assert.deepEqual(writes(f.state,'PATCH').map(r=>r[2].conclusion),['failure','success']);
    assert.equal(writes(f.state).length,0);
  }
});
test('暂态 API 故障在相同元数据恢复后重试，永久违规仍缓存为失败',async()=>{
  const transient=fixture();let fail=true;
  transient.state.onGet=()=>{if(fail){fail=false;throw new Error('temporary API failure');}};
  await cycles(transient,[()=>{},null]);
  assert.deepEqual(writes(transient.state,'PATCH').map(r=>r[2].conclusion),['failure','success']);
  const invalid=fixture();invalid.state.pulls[0].title='fix(api): bypass';
  await cycles(invalid,[()=>{},()=>{},()=>{},()=>{},()=>{},()=>{},null]);
  assert.equal(writes(invalid.state,'POST').length,1);
  assert.deepEqual(writes(invalid.state,'PATCH').map(r=>r[2].conclusion),['failure']);
});
test('正常成功缓存不重复完整验证，按轮询核对检查且跨有效期重新发布',async()=>{
  const f=fixture();
  await cycles(f,[()=>gate.CHECK_POLL_MS,()=>gate.CHECK_MAX_AGE_MS-gate.CHECK_POLL_MS,null]);
  assert.equal(writes(f.state,'POST').length,2);
  assert.deepEqual(writes(f.state,'PATCH').map(r=>r[2].conclusion),['success','success']);
  assert(f.state.requests.some(([method,url])=>method==='GET'&&url.includes('/check-runs?')));
});
test('检查缺失、取消、结果替换、时间异常和同名其他来源均会重新验证',async()=>{
  const mutations=[
    s=>{s.checkRuns=[];},
    s=>{s.checkRuns[0].status='queued';},
    s=>{s.checkRuns[0].conclusion='cancelled';},
    s=>{s.checkRuns.push({...s.checkRuns[0],id:999,conclusion:'failure'});},
    s=>{s.checkRuns[0].completed_at=null;},
    (s,_cycle,time)=>{s.checkRuns[0].completed_at=new Date(time-gate.CHECK_MAX_AGE_MS).toISOString();},
    s=>{s.checkRuns[0].app.id=15368;}
  ];
  for(const mutate of mutations){
    const f=fixture();
    await cycles(f,[(s,c,t)=>{mutate(s,c,t);return gate.CHECK_POLL_MS;},null]);
    assert.equal(writes(f.state,'POST').length,2);
    assert.equal(writes(f.state,'PATCH').at(-1)[2].conclusion,'success');
  }
});
test('替换检查触发的是完整复核，永久违规不会因为刷新而放行',async()=>{
  const f=fixture();f.state.pulls[0].body='缺少必需章节';
  await cycles(f,[s=>{s.checkRuns.push({...s.checkRuns[0],id:999,conclusion:'success'});return gate.CHECK_POLL_MS;},null]);
  assert.equal(writes(f.state,'POST').length,2);
  assert.deepEqual(writes(f.state,'PATCH').map(r=>r[2].conclusion),['failure','failure']);
});
test('检查发布失败不留下成功缓存，恢复后完成既有检查',async()=>{
  const f=fixture();let fail=true;
  f.state.onPatch=()=>{if(fail){fail=false;throw new Error('temporary check update failure');}};
  await cycles(f,[()=>{},null]);
  assert.equal(writes(f.state,'POST').length,1);
  assert.equal(writes(f.state,'PATCH').length,2);
  assert.equal(f.state.checkRuns.at(-1).conclusion,'success');
});
test('单个 HEAD 的发布故障不会饿死其他 HEAD，暂态持续故障采用有限退避',async()=>{
  const other=pull(45),sha='e'.repeat(40);other.head.sha=sha;
  const f=fixture([pull(36),other]);f.state.commitsByHead={[sha]:[{...f.state.commits[0],sha}]};
  f.state.onCreate=data=>{if(data.head_sha===HEAD)throw new Error('temporary check creation failure');};
  await cycles(f,[null]);
  assert.equal(f.state.checkRuns.length,1);assert.equal(f.state.checkRuns[0].head_sha,sha);
  assert.equal(f.state.checkRuns[0].conclusion,'success');
  const pending=fixture();pending.state.pulls[0].mergeable=null;
  await cycles(pending,[()=>{},()=>{},()=>{},()=>{},()=>{},()=>{},null]);
  assert.equal(writes(pending.state,'POST').length,3);
  assert(writes(pending.state,'PATCH').every(r=>r[2].conclusion==='failure'));
});

test('详情缺字段、提交列表不含当前 HEAD 和空文件对象均不能形成有效检查',async()=>{
  for(const mutate of [
    s=>{delete s.pulls[0].mergeable;},
    s=>{delete s.pulls[0].merge_commit_sha;},
    s=>{s.pulls[0].updated_at='invalid';},
    s=>{s.commits[0].sha='e'.repeat(40);},
    s=>{s.files=[null];}
  ]) {
    const f=fixture();mutate(f.state);
    await assert.rejects(gate.evaluate(f.client,36));
    assert.equal(writes(f.state).length,0);
  }
});
test('检查创建和完成响应必须对应本次 HEAD、来源和结论',async()=>{
  for(const [method,mutate] of [
    ['POST',r=>{r.head_sha=BASE;}],['POST',r=>{r.id=0;}],
    ['PATCH',r=>{r.id=999;}],['PATCH',r=>{r.app.id=15368;}],
    ['PATCH',r=>{r.conclusion='neutral';}],['PATCH',r=>{r.completed_at=null;}]
  ]) {
    const f=fixture(),request=f.client.request.bind(f.client);
    f.client.request=async(m,url,data)=>{
      const result=await request(m,url,data);
      if(m===method&&url.includes('/check-runs'))mutate(result);
      return result;
    };
    await assert.rejects(gate.merge(f.client,36),/检查/u);
    assert.equal(writes(f.state).length,0);
  }
});
test('检查完整分页选择本 App 最新结果，重复、截断和异常响应拒绝缓存',async()=>{
  const rows=Array.from({length:101},(_,i)=>({id:i+1,name:gate.CHECK,head_sha:HEAD,
    app:{id:APP},status:'completed',conclusion:'success',completed_at:'2026-11-07T00:00:00Z'}));
  for(const mutate of [null,
    r=>{r[100].id=1;},r=>{r[100].head_sha=BASE;},r=>{r[100].app.id=15368;}]) {
    const checks=structuredClone(rows);mutate?.(checks);const pages=[];
    const client=new gate.GitHub(async()=>'synthetic',{fetchImpl:async(url)=>{
      const page=Number(new URL(url).searchParams.get('page'));pages.push(page);
      return new Response(JSON.stringify({total_count:checks.length,check_runs:checks.slice((page-1)*100,page*100)}));
    }});client.appId=APP;
    if(mutate&&checks[100].app.id===APP)await assert.rejects(gate.latestCheck(client,HEAD),/检查/u);
    else assert.equal((await gate.latestCheck(client,HEAD)).id,mutate?100:101);
    assert.deepEqual(pages,[1,2]);
  }
  for(const result of [null,{total_count:2,check_runs:[rows[0]]},
    {total_count:101,check_runs:Array(101).fill(rows[0])},{total_count:1,check_runs:[null]}]) {
    const client={appId:APP,request:async()=>result};
    await assert.rejects(gate.latestCheck(client,HEAD),/检查/u);
  }
});
test('真实 HTTP 传输覆盖异步候选、暂态恢复及合并响应断连后的只读确认',async()=>{
  const f=fixture();f.state.realListing=true;let detailFailure=true,loseMerge=false;
  const prefix='/repos/'+gate.REPOSITORY;
  const server=http.createServer(async(req,res)=>{
    try {
      const url=new URL(req.url,'http://localhost');
      if(detailFailure&&req.method==='GET'&&url.pathname===prefix+'/pulls/36') {
        detailFailure=false;res.writeHead(503,{'Content-Type':'application/json'});
        res.end('{"message":"temporary"}');return;
      }
      let payload='';for await(const chunk of req)payload+=chunk;
      let result;
      if(req.method==='GET'&&url.pathname===prefix+'/pulls')result=await f.client.list(prefix+'/pulls?state=open');
      else if(req.method==='GET'&&/\/pulls\/\d+\/(commits|files)$/u.test(url.pathname))result=await f.client.list(url.pathname);
      else result=await f.client.request(req.method,url.pathname+(url.pathname.endsWith('/check-runs')?url.search:''),payload?JSON.parse(payload):undefined);
      if(loseMerge&&req.method==='PUT'&&url.pathname.endsWith('/merge')) {res.destroy();return;}
      res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(result));
    } catch {res.writeHead(500);res.end('{}');}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin='http://127.0.0.1:'+server.address().port;
  const client=new gate.GitHub(async()=>'synthetic',{fetchImpl:(url,options)=>{
    assert.equal(new URL(url).origin,'https://api.github.com');
    return fetch(origin+new URL(url).pathname+new URL(url).search,options);
  }});client.appId=APP;
  try {
    f.state.pulls[0].mergeable=null;f.state.pulls[0].merge_commit_sha=null;
    await cycles({client,state:f.state},[
      ()=>{},s=>{s.pulls[0].mergeable=true;s.pulls[0].merge_commit_sha=CANDIDATE;},null]);
    assert.deepEqual(writes(f.state,'PATCH').map(r=>r[2].conclusion),['failure','failure','success']);
    loseMerge=true;
    assert.deepEqual(await gate.merge(client,36),{sha:MERGED,merged:true});
    assert.equal(writes(f.state).length,1);
  } finally {
    server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
  }
});

test('确定性提交和文件超限失败在元数据不变时复用缓存',async()=>{
  for(const [field,limit] of [['commits',250],['changed_files',500]]) {
    const f=fixture();
    f.state.pulls[0][field]=limit+1;
    const report=await gate.evaluate(f.client,36);
    assert.equal(report.retry,false);
    assert.deepEqual(report.errors,['PR 提交或文件数量超出可核验范围']);
    await cycles(f,[()=>gate.CHECK_POLL_MS,()=>gate.CHECK_POLL_MS,null]);
    assert.equal(writes(f.state,'POST').length,1);
    assert.equal(writes(f.state,'PATCH').length,1);
    assert.equal(f.state.checkRuns[0].conclusion,'failure');
    assert(!f.state.requests.some(([method,url])=>method==='LIST'&&/\/(commits|files)$/u.test(url)));
  }
});

test('数量上限内的完整提交和文件列表仍通过',async()=>{
  const f=fixture();
  const message=f.state.commits[0].commit.message;
  f.state.commits=Array.from({length:250},(_,index)=>({
    sha:index===249?HEAD:(index+1).toString(16).padStart(40,'0'),commit:{message}
  }));
  f.state.files=Array.from({length:500},(_,index)=>({filename:'src/file-'+index+'.js'}));
  f.state.pulls[0].commits=f.state.commits.length;
  f.state.pulls[0].changed_files=f.state.files.length;
  const report=await gate.evaluate(f.client,36);
  assert.deepEqual(report.errors,[]);
  assert.equal(report.retry,false);
});

test('检查创建和完成持续失败保留退避并在恢复后完成同一检查',async()=>{
  for(const stage of ['create','complete']) {
    const f=fixture();
    let failing=true;
    f.state[stage==='create'?'onCreate':'onPatch']=()=>{
      if(failing)throw new Error('temporary check API failure');
    };
    const steps=Array.from({length:8},()=>()=>{});
    steps[6]=()=>{
      assert.equal(writes(f.state,stage==='create'?'POST':'PATCH').length,3);
      assert.equal(f.state.checkRuns.length,stage==='create'?0:1);
      failing=false;
    };
    await cycles(f,steps);
    assert.equal(writes(f.state,'POST').length,stage==='create'?4:1);
    assert.equal(writes(f.state,'PATCH').length,stage==='create'?1:4);
    assert.equal(f.state.checkRuns.length,1);
    assert.equal(f.state.checkRuns[0].status,'completed');
    assert.equal(f.state.checkRuns[0].conclusion,'success');
  }
});

test('检查创建或完成响应丢失后恢复既有检查且不重复创建',async()=>{
  for(const lostMethod of ['POST','PATCH']) {
    const f=fixture();
    const request=f.client.request.bind(f.client);
    let loseResponse=true;
    f.client.request=async(method,url,data)=>{
      const result=await request(method,url,data);
      if(loseResponse&&method===lostMethod&&url.includes('/check-runs')) {
        loseResponse=false;
        throw new Error('temporary check response loss');
      }
      return result;
    };
    await cycles(f,[()=>{},null]);
    assert.equal(writes(f.state,'POST').length,1);
    assert.equal(writes(f.state,'PATCH').length,lostMethod==='POST'?1:2);
    assert.equal(f.state.checkRuns.length,1);
    assert.equal(f.state.checkRuns[0].conclusion,'success');
  }
});

test('创建响应丢失后的恢复按关联标识查找，保留其他进程的检查',async()=>{
  const f=fixture();
  const request=f.client.request.bind(f.client);
  let loseResponse=true;
  f.client.request=async(method,url,data)=>{
    const result=await request(method,url,data);
    if(loseResponse&&method==='POST'&&url.endsWith('/check-runs')) {
      loseResponse=false;
      f.state.checkRuns.push({...structuredClone(result),id:999,external_id:'other-publication',
        status:'completed',conclusion:'success',completed_at:'2026-11-07T00:00:00Z'});
      throw new Error('temporary check response loss');
    }
    return result;
  };
  await cycles(f,[()=>{},null]);
  assert.equal(writes(f.state,'POST').length,1);
  assert.equal(writes(f.state,'PATCH').length,1);
  assert.equal(f.state.checkRuns.length,2);
  assert(f.state.checkRuns.every(check=>check.status==='completed'&&check.conclusion==='success'));
  assert(f.state.requests.some(([method,url])=>method==='GET'&&url.includes('&filter=all&')));
  assert.equal(f.state.checkRuns[1].external_id,'other-publication');
});

test('恢复未完成检查时重新验证变化后的 PR 元数据',async()=>{
  const f=fixture();
  let fail=true;
  f.state.onPatch=()=>{if(fail){fail=false;throw new Error('temporary check update failure');}};
  await cycles(f,[state=>{state.pulls[0].title='invalid';},null]);
  assert.equal(writes(f.state,'POST').length,1);
  assert.equal(writes(f.state,'PATCH').length,2);
  assert.equal(f.state.checkRuns.length,1);
  assert.equal(f.state.checkRuns[0].status,'completed');
  assert.equal(f.state.checkRuns[0].conclusion,'failure');
  assert.match(f.state.checkRuns[0].output.summary,/标题/u);
});
