'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const cp=require('node:child_process');
const root=path.join(__dirname,'..');
const gate=path.join(root,'.github','scripts','governance_gate.cjs');
const body='## 背景与目标\n当前提交缺少元数据合规检查。\n## 变更范围与实现\n添加严格的中文提交标题和PR结构检查。\n## 兼容性与风险\n不影响业务代码；合并前仍然需要独立审查。\n## 实际验证\n已经执行符合规定和失败输入的合成正反例检查。\n## 未完成事项\n远端的规则与必需状态检查仍需配置和验证。';
const validMessage='fix(api): 修正调用失败后的权限状态处理\n\n权限失败将终止本次请求，不再继续执行下游业务操作。\n回归验证受保护路径、失败响应与已有正常流程，兼容当前请求契约。';
const fixture={
  number:9,title:'fix(api): 修正调用失败后的权限状态处理',body,
  head:{sha:'a'.repeat(40),ref:'fix/api-state',repo:{full_name:'example/repo'}},
  base:{sha:'b'.repeat(40),ref:'main',repo:{full_name:'example/repo'}},
  merge_commit_sha:'c'.repeat(40)
};
const mockSource='const baseline='+JSON.stringify(fixture)+';\nconst message='+JSON.stringify(validMessage)+';\n'+String.raw`
const mode=process.env.GOV_TEST_MODE;
let reads=0;
globalThis.fetch=async function(url,opts){
  if(opts.headers.Authorization!=='Bearer synthetic-token') throw new Error('unexpected auth');
  const pathname=new URL(url).pathname;
  if(mode==='api-fail') return {ok:false,status:403};
  if(pathname.endsWith('/pulls/9')){
    const pr=structuredClone(baseline);
    reads++;
    const change=mode.replace(/^during-/, '');
    if(!mode.startsWith('during-')||reads>1){
      if(change==='bad-title') pr.title='fix(api): fix auth bug';
      if(change==='title') pr.title='fix(api): 修正调用成功后的权限状态处理';
      if(change==='body') pr.body+='\n当前说明已经发生新的有效修改。';
      if(change==='head-sha') pr.head.sha='d'.repeat(40);
      if(change==='head-ref') pr.head.ref='fix/another-state';
      if(change==='head-repo') pr.head.repo.full_name='other/repo';
      if(change==='base-sha') pr.base.sha='e'.repeat(40);
      if(change==='base-ref') pr.base.ref='release/another-base';
      if(change==='base-repo') pr.base.repo.full_name='other/repo';
      if(change==='candidate') pr.merge_commit_sha='f'.repeat(40);
      if(change==='missing-base') delete pr.base;
    }
    return {ok:true,json:async()=>pr};
  }
  if(pathname.endsWith('/pulls/9/commits')) return {ok:true,json:async()=>[
    {sha:baseline.head.sha,commit:{message:mode==='bad-commit'?'fix(api): fix auth bug':message}}
  ]};
  if(pathname.endsWith('/pulls/9/files')) return {ok:true,json:async()=>mode==='no-files'?[]:[{filename:'src/api.ts'}]};
  return {ok:false,status:404};
};
`;
const cases=[
  ['pr','pass',0,'PASS:'],
  ['pr','stale',1,'PR HEAD 已变化'],
  ['pr','bad-title',1,'PR：'],
  ['pr','bad-commit',1,'提交'],
  ['pr','no-files',1,'PR 缺少可核验的非空差异'],
  ['pr','api-fail',1,'GitHub API 返回 403'],
  ['pr','missing-base',1,'缺少可核验状态'],
  ['event-current','pass',0,'PASS:'],
  ['event-current','candidate',1,'合并候选与本次运行不一致'],
  ['event-current','api-fail',1,'GitHub API 返回 403']
];
for(const [change,label] of [
  ['title','标题'],['body','正文'],['head-sha','HEAD'],['head-ref','源分支'],
  ['head-repo','来源仓库'],['base-sha','BASE'],['base-ref','目标分支'],['base-repo','目标仓库']
]){
  cases.push(['event-current',change,1,'PR '+label+' 已变化']);
  cases.push(['pr','during-'+change,1,'PR '+label+' 已变化']);
}
cases.push(['pr','during-candidate',1,'合并候选在校验期间改变']);

test('当前 PR 状态必须与事件、合并候选和读取期间的状态一致',()=>{
  const work=fs.mkdtempSync(path.join(os.tmpdir(),'governance-check-'));
  try{
    const helper=path.join(work,'mock-api.cjs');
    const event=path.join(work,'event.json');
    fs.writeFileSync(helper,mockSource,'utf8');
    for(const [command,mode,expected,diagnostic] of cases){
      const payload=structuredClone(fixture);
      if(mode==='stale') payload.head.sha='d'.repeat(40);
      if(mode==='bad-title') payload.title='fix(api): fix auth bug';
      fs.writeFileSync(event,JSON.stringify({repository:{full_name:'example/repo'},pull_request:payload}),'utf8');
      const result=cp.spawnSync(process.execPath,['--require',helper,gate,command],{
        cwd:root,encoding:'utf8',timeout:15000,windowsHide:true,
        env:{...process.env,GOV_TEST_MODE:mode,GITHUB_EVENT_PATH:event,
          GITHUB_TOKEN:'synthetic-token',GITHUB_SHA:fixture.merge_commit_sha}
      });
      const output=command+' '+mode+': '+result.stdout+result.stderr;
      assert.equal(result.status,expected,output);
      assert.ok((result.stdout+result.stderr).includes(diagnostic),output);
    }
  }finally{
    fs.rmSync(work,{recursive:true,force:true});
  }
});
