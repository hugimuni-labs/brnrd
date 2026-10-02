import React from 'react';
import {AbsoluteFill, Audio, staticFile, useCurrentFrame} from 'remotion';

/* All geometry is generated here. Coordinates are actual three-dimensional
   points; the changing basis reprojects the same graph, never swaps a card grid. */
type V=[number,number,number];
type Q=[number,number];
const W=1920,H=1080;
const WHITE='#f6f2e8',BLUE='#83bdff',ORANGE='#e5854b',AMBER='#ecae5b',RED='#f65940';
const clamp=(n:number,a=0,b=1)=>Math.max(a,Math.min(b,n));
const mix=(a:number,b:number,t:number)=>a+(b-a)*t;
const ease=(x:number)=>{const t=clamp(x);return t*t*t*(t*(t*6-15)+10)};
const ramp=(t:number,a:number,b:number)=>ease((t-a)/(b-a));
const window=(t:number,a:number,b:number,f=.5)=>ramp(t,a,a+f)*(1-ramp(t,b-f,b));
const add=(a:V,b:V):V=>[a[0]+b[0],a[1]+b[1],a[2]+b[2]];
const vm=(a:V,b:V,t:number):V=>[mix(a[0],b[0],t),mix(a[1],b[1],t),mix(a[2],b[2],t)];
const hash=(i:number)=>{const s=Math.sin(i*127.1+311.7)*43758.5453;return s-Math.floor(s)};
const pts=(a:Q[])=>a.map(p=>p.map(x=>x.toFixed(2)).join(',')).join(' ');
const fmt=(n:number)=>n.toFixed(2);

type Camera={yaw:number,pitch:number,roll:number,scale:number,x:number,y:number,warp?:number};
function project(p:V,c:Camera):Q{
 let [x,y,z]=p;
 if(c.warp){const w=c.warp;x+=Math.sin(z*1.2+y*.7)*w;y+=Math.sin(x*.65+z)*w*.7;z+=Math.sign(x)*w*.32;}
 const xx=x*Math.cos(c.yaw)+z*Math.sin(c.yaw);
 const zz=-x*Math.sin(c.yaw)+z*Math.cos(c.yaw);
 const yy=y*Math.cos(c.pitch)-zz*Math.sin(c.pitch);
 const depth=y*Math.sin(c.pitch)+zz*Math.cos(c.pitch);
 const per=1/(1+depth*.021);
 const X=xx*c.scale*per,Y=yy*c.scale*per;
 return [c.x+X*Math.cos(c.roll)-Y*Math.sin(c.roll),c.y+X*Math.sin(c.roll)+Y*Math.cos(c.roll)];
}
function line(a:V,b:V,c:Camera,color:string,opacity=1,width=1.3,key?:string){
 const A=project(a,c),B=project(b,c);
 return <line key={key} x1={A[0]} y1={A[1]} x2={B[0]} y2={B[1]} stroke={color} opacity={opacity} strokeWidth={width} strokeLinecap="round"/>;
}
function poly(a:V[],c:Camera,fill:string,stroke:string,opacity=1,width=1){return <polygon points={pts(a.map(v=>project(v,c)))} fill={fill} stroke={stroke} strokeWidth={width} opacity={opacity}/>}
function box(pos:V,size:V,c:Camera,color:string,alpha=.35,solid=false){
 const [x,y,z]=pos,[sx,sy,sz]=size;
 const v:V[]=[[x,y,z],[x+sx,y,z],[x+sx,y,z+sz],[x,y,z+sz],[x,y-sy,z],[x+sx,y-sy,z],[x+sx,y-sy,z+sz],[x,y-sy,z+sz]];
 return <g>{poly([v[0],v[1],v[5],v[4]],c,solid?color:'url(#glass)',color,alpha,1.1)}{poly([v[1],v[2],v[6],v[5]],c,solid?color:'#121b25',color,alpha*.8,1.1)}{poly([v[4],v[5],v[6],v[7]],c,solid?color:'url(#plate)',color,alpha*1.25,1.1)}{line(v[4],v[5],c,color,alpha*1.5,2)}{line(v[5],v[6],c,color,alpha*1.1,1.4)}</g>
}
function Plane({c,y,color,extent=9,alpha=.2,grid=true}:{c:Camera,y:number,color:string,extent?:number,alpha?:number,grid?:boolean}){
 const e=extent;
 return <g>{poly([[-e,y,-e],[e,y,-e],[e,y,e],[-e,y,e]],c,'url(#plane)',color,alpha,1)}{grid&&Array.from({length:19},(_,i)=>{const k=(i-9)*e/9;return <g key={i}>{line([-e,y,k],[e,y,k],c,color,alpha*.48)}{line([k,y,-e],[k,y,e],c,color,alpha*.48)}</g>})}</g>
}
function Prism({p,c,size=.33,opacity=1,red=false}:{p:V,c:Camera,size?:number,opacity?:number,red?:boolean}){
 const s=size;
 const vs:V[]=[add(p,[0,-s*1.7,0]),add(p,[-s,0,0]),add(p,[0,0,s]),add(p,[s,0,0]),add(p,[0,0,-s]),add(p,[0,s*1.7,0])];
 const center=project(p,c);
 return <g opacity={opacity}>
 <ellipse cx={center[0]} cy={center[1]+21} rx={45*size} ry={12*size} fill={red?RED:WHITE} opacity=".12" filter="url(#glow)"/>
 {poly([vs[0],vs[1],vs[2]],c,red?'#ff8969':'#f8f8f1',WHITE,1,.8)}
 {poly([vs[0],vs[2],vs[3]],c,red?'#e44638':'#acbccc',WHITE,1,.8)}
 {poly([vs[2],vs[3],vs[5]],c,red?'#90201e':'#627889',WHITE,1,.8)}
 {poly([vs[1],vs[2],vs[5]],c,red?'#b53628':'#c7d0d1',WHITE,1,.8)}
 {line(add(p,[0,-s*.9,s*.32]),add(p,[0,s*.9,s*.32]),c,'#0b1118',1,3)}
 <circle cx={center[0]} cy={center[1]} r="3" fill="#090e13"/>
 </g>
}
function Text({x,y,size=28,children,color=WHITE,opacity=1,spacing=0,serif=false,weight=500,anchor='start'}:{x:number,y:number,size?:number,children:React.ReactNode,color?:string,opacity?:number,spacing?:number,serif?:boolean,weight?:number,anchor?:'start'|'middle'|'end'}){
 return <text x={x} y={y} fill={color} opacity={opacity} fontSize={size} fontWeight={weight} letterSpacing={spacing} fontFamily={serif?'Georgia, serif':'Arial, Helvetica, sans-serif'} textAnchor={anchor}>{children}</text>
}
const branch:V[]=[[-4.5,0,0],[-3,0,0],[-1.45,0,0],[.1,0,0],[.1,0,2],[2,0,2],[3.8,0,2]];
const edges=[[0,1],[1,2],[2,3],[3,4],[4,5],[5,6]];
function Spine({c,t,color=WHITE,upto=6,old=false}:{c:Camera,t:number,color?:string,upto?:number,old?:boolean}){
 return <g>{edges.map(([a,b],i)=>{const reveal=clamp((upto-i));return reveal>0&&line(branch[a],vm(branch[a],branch[b],reveal),c,color,old?.22:.9,i===2?2:3,String(i))})}
 {branch.map((p,i)=>i<=Math.ceil(upto)&&<g key={i}>{box(add(p,[-.075,.075,-.075]),[.15,.15,.15],c,color,old?.25:.9,true)}{i===5&&!old&&<g>{(()=>{const q=project(add(p,[0,-.65,0]),c);return <><Text x={q[0]} y={q[1]} size={22} spacing={3} anchor="middle">PR / 07</Text><line x1={q[0]} x2={q[0]} y1={q[1]+10} y2={q[1]+33} stroke={color} opacity=".5"/></>})()}</g>}</g>)}
 </g>
}
function Chamber({c,t,kind}:{c:Camera,t:number,kind:number}){
 const color=kind===0?BLUE:kind===1?ORANGE:AMBER;
 return <g>
 <Plane c={c} y={1.6} color={color} extent={12} alpha={kind===1?.12:.21}/>
 {kind===0&&<>
 {Array.from({length:18},(_,i)=>{const x=(i%6)*2.7-8,z=Math.floor(i/6)*5-6,h=1.8+hash(i)*4;return <g key={i}>{box([x,1.5,z],[.55,h,1.6],c,BLUE,.22)}{line([x+.25,1.4,z+.8],[x+.25,1.4-h,z+.8],c,BLUE,.1,8)}</g>})}
 {poly([[5,-4,-4],[5,2,-4],[5,2,5],[5,-4,5]],c,'url(#glass)',BLUE,.45,1)}
 </>}
 {kind===1&&<>
 {Array.from({length:8},(_,i)=>{const z=-5+i*1.4;return <g key={i}>{poly([[-7,-1,z],[7,-1,z], [7,-1,z+.6],[-7,-1,z+.6]],c,'#c2b8a1','#d3c6aa',.1+.015*i,.5)}{line([-7,-1,z],[7,-1,z],c,ORANGE,.28,1)}</g>})}
 {Array.from({length:6},(_,i)=>box([-6+i*2.4,1.5,-5],[1.2,2.3+Math.sin(i)*.9,.4],c,ORANGE,.19))}
 </>}
 {kind===2&&<>
 {Array.from({length:70},(_,i)=>{const x=(i%14)-7,z=Math.floor(i/14)*1.8-4;return <g key={i}>{box([x,1.5,z],[.7,.35+Math.floor(hash(i+5)*7)*.45,.7],c,i%5===0?RED:AMBER,.3)}{i%4===0&&line([x,1.5,z],[x,1.5,-8],c,AMBER,.22)}</g>})}
 </>}
 </g>
}
function Intro({t}:{t:number}){
 const c:Camera={yaw:mix(.3,-.48,ramp(t,0,12)),pitch:.73,roll:mix(-.06,0,ramp(t,0,6)),scale:mix(215,125,ramp(t,0,6)),x:1110,y:580};
 const upto=mix(.01,5.999,ramp(t,2,9.8));
 const coldFade=1-ramp(t,16.6,17.1);
 const follow=ramp(t,12,14.1);
 return <g opacity={coldFade}>
 <Chamber c={c} t={t} kind={0}/>
 <g opacity={.32}>{line([-8,-.02,0],[8,-.02,0],c,BLUE,.55,1)}</g>
 <Spine c={c} t={t} upto={t<12?upto:6}/>
 <Prism p={t<10?vm(branch[0],branch[5],ramp(t,2,10)):vm(branch[5],add(branch[6],[1.2,0,0]),follow)} c={c}/>
 {t>12&&<g opacity={ramp(t,12,13)}>
 {poly([[4.95,-5,-5],[4.95,2,-5],[4.95,2,6],[4.95,-5,6]],c,'#101925',BLUE,.68,1.2)}
 {Array.from({length:23},(_,i)=>line([4.94,-5+i*.3,-5],[4.94,-5+i*.3,6],c,BLUE,.45,1))}
 {(()=>{const q=project([5.05,-2,1.6],c);return <g opacity={ramp(t,13.3,14)}><Text x={q[0]-90} y={q[1]-15} size={27} color={BLUE} spacing={3}>USAGE LIMIT</Text><line x1={q[0]-90} y1={q[1]+12} x2={q[0]+118} y2={q[1]+12} stroke={BLUE} strokeWidth={2}/></g>})()}
 </g>}
 <Text x={148} y={238} size={18} spacing={5} color={BLUE} opacity={ramp(t,.5,1.5)}>01 / THE ROOM</Text>
 <Text x={143} y={335} size={84} weight={700} spacing={-4} opacity={window(t,1,12,.7)}>Codex.</Text>
 <Text x={148} y={394} size={22} spacing={3} color={BLUE} opacity={window(t,3,11,.5)}>BUILD → BRANCH → PR</Text>
 <Text x={143} y={338} size={70} weight={700} spacing={-3} opacity={window(t,12,17,.45)}>Follow-up.</Text>
 <Text x={148} y={404} size={29} opacity={window(t,14.3,17,.35)}>The work stops at the wall.</Text>
 <Text x={148} y={462} size={24} color={BLUE} spacing={4} opacity={window(t,14.1,17,.2)}>USAGE LIMIT.</Text>
 <Text x={148} y={875} size={16} spacing={4} color={BLUE}>WORK / 07</Text>
 </g>
}
function Carry({t}:{t:number}){
 const u=t-17;
 const c:Camera={yaw:mix(-.5,.58,ramp(u,0,8)),pitch:mix(.74,.43,ramp(u,0,8)),roll:Math.sin(u*.32)*.03,scale:127,x:1140,y:600};
 return <g opacity={window(t,17,30,.3)}>
 <Chamber c={c} t={t} kind={1}/>
 <g opacity={1-ramp(t,23,25)}>
 {Array.from({length:10},(_,i)=>{const x=-5+i*.9,fold=Math.sin(i*Math.PI*.62+u*.36)*.48;
 return <g key={i}>{poly([[x,-1+fold,-1.4],[x+.88,-1-fold,-1.4],[x+.88,-1-fold,2.4],[x,-1+fold,2.4]],c,'#e3dcc8','#f7efe0',.7,1)}{line([x+.15,-1+fold,-.8],[x+.67,-1-fold,-.8],c,'#725342',.5,1.5)}{line([x+.15,-1+fold,0],[x+.67,-1-fold,0],c,'#725342',.5,1.5)}{line([x+.15,-1+fold,.8],[x+.67,-1-fold,.8],c,'#725342',.5,1.5)}</g>})}
 </g>
 <g opacity={ramp(t,22.5,25)}><Spine c={c} t={t} upto={6} color={WHITE}/></g>
 <Prism p={[-3.8+ramp(t,17.2,23)*6,-1.5+ramp(t,23,26)*1.5,1.2+ramp(t,23,26)*.8]} c={c}/>
 <Text x={148} y={237} size={18} spacing={5} color={ORANGE}>02 / THE HANDOFF</Text>
 <g opacity={window(t,17.2,24.6,.4)}>
 <Text x={140} y={338} size={66} weight={500} serif>You carry</Text>
 <Text x={140} y={417} size={66} weight={500} serif>the context.</Text>
 <Text x={148} y={485} size={22} color={ORANGE} spacing={2}>BY HAND. AGAIN.</Text>
 </g>
 <g opacity={window(t,24.6,30,.4)}>
 <Text x={142} y={337} size={85} serif>Claude.</Text>
 <Text x={148} y={407} size={27}>The same work. Restitched.</Text>
 </g>
 <Text x={148} y={875} size={16} spacing={4} color={ORANGE}>WORK / 07</Text>
 </g>
}
const GLYPHS:Record<string,string[]>={
 M:['10001','11011','10101','10101','10001','10001','10001'],
 I:['11111','00100','00100','00100','00100','00100','11111'],
 S:['11111','10000','10000','11111','00001','00001','11111'],
 T:['11111','00100','00100','00100','00100','00100','00100'],
 R:['11110','10001','10001','11110','10100','10010','10001'],
 A:['01110','10001','10001','11111','10001','10001','10001'],
 L:['10000','10000','10000','10000','10000','10000','11111']};
function Bitmap({t}:{t:number}){return <g>{'MISTRAL'.split('').map((ch,k)=>GLYPHS[ch].map((row,j)=>row.split('').map((v,i)=>v==='1'&&<rect key={`${k}-${j}-${i}`} x={148+k*48+i*8} y={277+j*8} width={7} height={7} fill={WHITE} opacity={ramp(t,30+k*.02,30.2+k*.02)}/>)))}</g>}
function Wrong({t}:{t:number}){
 const u=t-30;
 const c:Camera={yaw:mix(-.37,.12,ramp(u,0,6)),pitch:.8,roll:0,scale:123,x:1100,y:580};
 const wrongPath:V[]=[branch[0],branch[1],[-3,0,-2],[-.6,0,-2],[2,0,-2],[4,0,-2]];
 return <g opacity={window(t,30,36.2,.25)}>
 <Chamber c={c} t={t} kind={2}/>
 <g opacity=".72"><Spine c={c} t={t} color={WHITE} old/></g>
 {(()=>{const q=project(add(branch[5],[0,-.6,0]),c);return <Text x={q[0]} y={q[1]} color={WHITE} opacity={.48} size={22} spacing={3} anchor="middle">PR / 07</Text>})()}
 {wrongPath.slice(0,-1).map((p,i)=>{const r=ramp(t,30.7+i*.5,31.5+i*.5);return line(p,vm(p,wrongPath[i+1],r),c,i>1?RED:AMBER,.9,3,String(i))})}
 {wrongPath.map((p,i)=>box(add(p,[-.07,.07,-.07]),[.14,.14,.14],c,RED,ramp(t,31+i*.3,31.3+i*.3),true))}
 <Prism p={vm(wrongPath[0],wrongPath[5],ramp(t,31,33.7))} c={c} red/>
 {(()=>{const q=project([4,-.75,-2],c);return <g opacity={ramp(t,33.5,34)}><Text x={q[0]+25} y={q[1]-40} color={RED} size={24} spacing={3}>RELEASE / 07</Text><Text x={q[0]+25} y={q[1]} color={WHITE} size={25}>Wrong lineage.</Text></g>})()}
 <Text x={148} y={237} size={18} spacing={5} color={AMBER}>03 / THE RESET</Text>
 <Bitmap t={t}/>
 <Text x={148} y={397} size={24} color={AMBER} spacing={3}>FRESH SESSION.</Text>
 <Text x={148} y={445} size={27} opacity={ramp(t,32,33)}>No memory of the branch.</Text>
 <Text x={148} y={875} size={16} spacing={4} color={AMBER}>WORK / 07</Text>
 </g>
}
// A fixed, reproducible network: 72 architectural modules, 130 rails.
const nodes:V[]=Array.from({length:72},(_,i)=>{const layer=Math.floor(i/24),j=i%24;return [(j%6-2.5)*2.3,layer*2.35-2.3,(Math.floor(j/6)-1.5)*2.6]});
function embedding(i:number,mode:number):V {
 const p=nodes[i],layer=Math.floor(i/24),j=i%24;
 if(mode===1) return [p[0]*.92,p[1]*.68+(j%6)*.075,p[2]*.62];
 if(mode===2) return [p[0]*.69+(layer-1)*3.9,p[1]*.55-hash(i)*.7,p[2]*.88];
 if(mode===3) {const a=j*Math.PI*2/24,r=4.7+layer*1.15;return [Math.cos(a)*r,layer*1.9-2.3+(j%6)*.13,Math.sin(a)*r];}
 return p;
}
function positioned(i:number,t:number):V {
 if(t<48.4)return embedding(i,0);
 if(t<49.3)return vm(embedding(i,0),embedding(i,1),ramp(t,48.4,49.3));
 if(t<54.6)return embedding(i,1);
 if(t<55.3)return vm(embedding(i,1),embedding(i,2),ramp(t,54.6,55.3));
 if(t<60.6)return embedding(i,2);
 if(t<61.3)return vm(embedding(i,2),embedding(i,3),ramp(t,60.6,61.3));
 return embedding(i,3);
}
const railPairs:number[][]=[];
nodes.forEach((_,i)=>{if(i%6<5)railPairs.push([i,i+1]);if(i%24<18)railPairs.push([i,i+6]);if(i<48&&i%3===0)railPairs.push([i,i+24]);});
const WORLD_KEYS=[
 [43,-.70,.69,-.05,91,990,620],
 [48,-.35,.58,.02,110,1060,590],
 [49.2,.91,.35,-.18,85,960,550],
 [54.3,1.15,.52,-.03,112,940,590],
 [55.3,2.70,.81,.13,95,985,610],
 [60.2,2.95,.58,.03,108,920,600],
 [61.3,4.52,.43,-.14,93,970,570],
 [65.9,4.88,.66,.02,110,950,590],
 [69.9,5.36,.89,0,63,960,556],
];
function worldCamera(t:number):Camera{
 let a=WORLD_KEYS[0],b=a;
 for(let i=0;i<WORLD_KEYS.length-1;i++){if(t>=WORLD_KEYS[i][0]){a=WORLD_KEYS[i];b=WORLD_KEYS[i+1];}}
 const r=ease((t-a[0])/(b[0]-a[0]||1));
 return {yaw:mix(a[1],b[1],r),pitch:mix(a[2],b[2],r),roll:mix(a[3],b[3],r),scale:mix(a[4],b[4],r),x:mix(a[5],b[5],r),y:mix(a[6],b[6],r)};
}
function Topology({t,c,mode=0,fault=0}:{t:number,c:Camera,mode?:number,fault?:number}){
 const palette=[BLUE,'#b8c8bd','#cea8a4','#88afb6'];
 const color=palette[mode];
 const resident:V=[Math.sin(t*.32)*1.4,-.15,Math.cos(t*.24)*1.2];
 const C={...c,warp:fault};
 // Track the invariant: world translates around it; its shape never changes.
 const r=project(resident,C),dx=960-r[0],dy=520-r[1];
 const cc={...C,x:C.x+dx,y:C.y+dy};
 return <g>
 {mode===3&&[4.7,5.85,7].map((r,i)=>{const ring:V[]=Array.from({length:49},(_,j)=>[Math.cos(j*Math.PI*2/48)*r,i*1.9-2.3,Math.sin(j*Math.PI*2/48)*r]);return <polyline key={i} points={pts(ring.map(v=>project(v,cc)))} fill="none" stroke={color} strokeWidth="1.5" opacity=".4"/>})}
 {[-3.8,-1.1,1.6,4.3].map((y,i)=><Plane key={i} c={cc} y={y} color={color} extent={9.2} alpha={.20} />)}
 {railPairs.map(([i,j],k)=>{
 const p=positioned(i,t),q=positioned(j,t),bend:V=[q[0],p[1],p[2]];
 const bright=(Math.floor(t*3)+k)%37===0;
 return <g key={k}>{line(p,bend,cc,bright?WHITE:color,bright?.9:.36,bright?2.6:1)}{line(bend,q,cc,color,.36,1)}
 {k%7===0&&line(add(p,[0,-.16,0]),add(q,[0,-.16,0]),cc,color,.18,.6)}
 </g>})}
 {nodes.map((p,i)=>{
 const h=.35+hash(i+2)*1.65; p=positioned(i,t);
 const lift=Math.sin(t*.5+i*.61)*.09;
 const sh=(mode===0?1:mode===1?1.65:mode===2?1.2:.65);
 const pp=add(p,[0,lift,0]);
 return <g key={i}>
 {box(pp,[mode===1?.48:.62+h*.25,h*sh,mode===1?1.1:.72],cc,i%13===0?WHITE:color,i%13===0?.60:.37)}
 {i%6===0&&box(add(pp,[-.15,.02,-.12]),[1.2,.03,1.12],cc,color,.3)}
 {mode===1&&i%5===0&&Array.from({length:4},(_,j)=>line(add(pp,[0,-j*.21,0]),add(pp,[.65,-j*.21,0]),cc,WHITE,.35))}
 {mode===2&&i%4===0&&line(add(pp,[0,-h,0]),add(pp,[1.8,-h,1.7]),cc,color,.4)}
 </g>
 })}
 {Array.from({length:12},(_,i)=>{const p:V=[(i%4-1.5)*5,-4.6, (Math.floor(i/4)-1)*6];return <g key={i}>{line(p,add(p,[0,10,0]),cc,color,.16)}{box(add(p,[-.06,0,-.06]),[.12,.2,.12],cc,WHITE,.5,true)}</g>})}
 {Array.from({length:18},(_,i)=>{
 const phase=(t*.35+i*.071)%1;
 const [a,b]=railPairs[(i*7)%railPairs.length];
 const p=vm(positioned(a,t),positioned(b,t),phase),q=project(p,cc);
 return <circle key={i} cx={q[0]} cy={q[1]} r={1.7} fill={WHITE} opacity=".68"/>
 })}
 {[-1,1].map((k,i)=>{const p=add(resident,[k*1.1,0,k*.7]);return <g key={i}>{line(p,resident,cc,WHITE,.85,2)}{box(add(p,[-.08,.08,-.08]),[.16,.16,.16],cc,WHITE,.85,true)}</g>})}
 <g filter="url(#softGlow)">{Prism({p:resident,c:cc,size:.44})}</g>
 <Prism p={resident} c={cc} size={.44}/>
 <ellipse cx={960} cy={520} rx={96} ry={26} fill="none" stroke={WHITE} strokeWidth=".75" opacity=".2" transform={`rotate(${Math.sin(t*.3)*14} 960 520)`}/>
 </g>
}
function Fault({t}:{t:number}){
 const u=t-36;
 const gain=ramp(t,36,38.5),open=ramp(t,39,42.6);
 const c:Camera={yaw:-.15+u*.18+Math.sin(u*2)*gain*.12,pitch:.75+Math.sin(u*.8)*.2,roll:Math.sin(u*.9)*gain*.12,scale:105+Math.sin(u)*10,x:960,y:570,warp:gain*(1-open)*.75};
 const faultLine=`M ${780+u*15} 80 L ${900+Math.sin(u)*70} 312 L ${815+u*12} 448 L ${1060-u*8} 573 L ${981+u*7} 730 L ${1130-u*7} 1000`;
 return <g opacity={window(t,36,43.05,.12)}>
 <g opacity={.5*(1-open)}><Text x={250} y={430} size={190} weight={700} color={AMBER} opacity={.14}>MISTRAL</Text><Text x={1210} y={776} size={70} color={BLUE} opacity={.18}>CODEX</Text><Text x={315} y={865} size={91} serif color={ORANGE} opacity={.15}>Claude.</Text></g>
 {[0,1,2].map(i=>{
 const shift=(i-1)*gain*64*(1-open),rot=(i-1)*gain*2;
 return <g key={i} clipPath={`url(#slice${i})`}><g transform={`translate(${shift} ${(i-1)*gain*31}) rotate(${rot} 960 540)`}><Topology c={c} t={t} mode={i===0?0:i===1?2:3} fault={gain*(1-open)*.8}/></g></g>
 })}
 <g opacity={gain*(1-open)}>
 <path d={faultLine} stroke="#8dadc8" strokeWidth="16" fill="none" opacity=".15" filter="url(#glow)"/>
 <path d={faultLine} stroke="#090d12" strokeWidth={13+gain*25} fill="none"/>
 <path d={faultLine} transform="translate(-3 0)" stroke="#82c5ce" strokeWidth="1.1" fill="none" opacity=".85"/>
 <path d={faultLine} transform="translate(3 0)" stroke="#e7a182" strokeWidth="1.1" fill="none" opacity=".9"/>
 {Array.from({length:20},(_,i)=>{const y=160+i*37,x=880+Math.sin(i*.8+u)*110;return <g key={i}><line x1={x-55} x2={x+45} y1={y} y2={y-13} stroke={i%2?'#e7a182':'#82c5ce'} strokeWidth=".7" opacity=".4"/></g>})}
 </g>
 <rect x="0" y="138" width="1920" height="804" fill={WHITE} opacity={window(t,38.37,38.47,.03)*.6}/>
 <g opacity={window(t,39.1,42.6,.3)}><Text x={148} y={251} size={19} spacing={5}>THE FAULT HAS A NAME.</Text><Text x={141} y={369} size={126} weight={700} spacing={-7}>brnrd</Text></g>
 </g>
}
function System({t}:{t:number}){
 const mode=t<49?0:t<55?1:t<61?2:3;
 const label=['PROVIDERS','MACHINES','PROJECTS','PROCESSES'][mode];
 const starts=[43,49.3,55.3,61.3];
 const subtitle=['THE SHELL CHANGES.','THE MACHINE CHANGES.','THE PROJECT CHANGES.','THE PROCESS CHANGES.'][mode];
 const c=worldCamera(t);
 return <g opacity={window(t,42.9,70,.2)}>
 <Topology c={c} t={t} mode={mode}/>
 <g opacity={window(t,starts[mode]+.2,starts[mode]+4.3,.45)}>
 <Text x={148} y={232} size={18} spacing={5} color={'#a8b4bc'}>ONE TOPOLOGY / {String(mode+1).padStart(2,'0')}</Text>
 <Text x={143} y={311} size={56} weight={700} spacing={-1}>{label}</Text>
 <Text x={149} y={359} size={18} spacing={3} color={'#abb9c4'}>{subtitle}</Text>
 </g>
 <Text x={960} y={841} size={22} anchor="middle" spacing={5} opacity={window(t,44.6,48.2,.5)}>THE RESIDENT REMAINS.</Text>
 {t>66&&<g opacity={ramp(t,66.7,67.5)}>
 <Text x={960} y={270} size={23} anchor="middle" spacing={5}>EVERYTHING CAN MOVE.</Text>
 <Text x={960} y={850} size={23} anchor="middle" spacing={5}>EXCEPT WHAT YOU OWN.</Text>
 </g>}
 </g>
}
function Kinetic({words,start,t,y,size=108}:{words:string,start:number,t:number,y:number,size?:number}) {
 const enter=ramp(t,start,start+.42),drift=1-enter;
 return <g transform={`translate(${960} ${y}) scale(${1+drift*.13} 1)`}>
 {[4,3,2,1].map(i=><text key={i} x={-i*drift*21} y={i*drift*13} fontSize={size} fontWeight="700" letterSpacing={-4+drift*18} fontFamily="Arial, sans-serif" textAnchor="middle" fill="none" stroke={BLUE} strokeWidth=".65" opacity={drift*.35}>{words}</text>)}
 <text x="0" y={drift*55} fill={WHITE} fontSize={size} fontWeight="700" letterSpacing={-4+drift*18} fontFamily="Arial, sans-serif" textAnchor="middle">{words}</text>
 </g>
}
function End({t}:{t:number}){
 const u=t-70;
 const c:Camera={yaw:.72+u*.04,pitch:.55,roll:0,scale:240,x:960,y:580};
 const endFade=1-ramp(t,82.8,84);
 return <g opacity={endFade}>
 <g opacity={.15*(1-ramp(t,71,73))}><Topology c={{...c,scale:65}} t={t} mode={3}/></g>
 <g opacity={window(t,70,73.7,.4)}>
 <Text x={960} y={421} size={23} spacing={5} anchor="middle" color={'#96a5ae'}>THE LINE OUTLIVES THE ROOM.</Text>
 <Prism p={[0,0,0]} c={c} size={.33}/>
 <Text x={960} y={779} size={15} spacing={5} anchor="middle">WORK / 07</Text>
 </g>
 <g opacity={window(t,73.5,77,.25)}>
 <Kinetic words="THE MODEL" start={73.5} t={t} y={455}/>
 <Kinetic words="IS REPLACEABLE." start={73.58} t={t} y={580}/>
 <line x1={700} x2={1220} y1={630} y2={630} stroke={BLUE} strokeWidth="1.2"/>
 </g>
 <g opacity={window(t,77,80.1,.22)}>
 <Kinetic words="THE WORK" start={77} t={t} y={455} size={132}/>
 <Kinetic words="IS YOURS." start={77.08} t={t} y={600} size={132}/>
 <line x1={700} x2={1220} y1={638} y2={638} stroke={WHITE} strokeWidth="2"/>
 </g>
 <g opacity={ramp(t,80,80.3)}>
 <Text x={960} y={388} size={23} spacing={8} anchor="middle">brnrd</Text>
 <Kinetic words="OWN YOUR AGENTS." start={80} t={t} y={555} size={114}/>
 <Text x={960} y={685} size={19} spacing={5} anchor="middle" color={'#97a5ad'}>CONTINUITY IS A FORM OF POWER.</Text>
 </g>
 </g>
}
export const Film:React.FC=()=>{
 const frame=useCurrentFrame(),t=frame/30;
 const warm=window(t,17,30,.3);
 const amber=window(t,30,38,.3);
 const bg=warm?'#181713':amber?'#160e0b':'#080d13';
 return <AbsoluteFill style={{backgroundColor:'#05080b'}}>
 <Audio src={staticFile('score.wav')}/>
 <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
 <defs>
 <linearGradient id="glass" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#b6cfe1" stopOpacity=".2"/><stop offset=".42" stopColor="#445765" stopOpacity=".02"/><stop offset="1" stopColor="#b6cfe1" stopOpacity=".15"/></linearGradient>
 <linearGradient id="plate" x1="0" y1="0" x2=".7" y2="1"><stop stopColor="#b6cfe1" stopOpacity=".55"/><stop offset="1" stopColor="#445765" stopOpacity=".01"/></linearGradient>
 <linearGradient id="plane"><stop stopColor="#8199a9" stopOpacity=".03"/><stop offset=".55" stopColor="#647788" stopOpacity=".08"/><stop offset="1" stopColor="#283342" stopOpacity=".015"/></linearGradient>
 <radialGradient id="ambient"><stop stopColor={warm?'#6e4a30':amber?'#894122':'#325172'} stopOpacity=".32"/><stop offset="1" stopColor={bg} stopOpacity="0"/></radialGradient>
 <radialGradient id="vignette"><stop offset=".36" stopColor="#000" stopOpacity="0"/><stop offset="1" stopColor="#000" stopOpacity=".50"/></radialGradient>
 <filter id="glow"><feGaussianBlur stdDeviation="16"/></filter>
 <filter id="softGlow"><feGaussianBlur stdDeviation="4"/></filter>
 <clipPath id="film"><rect x="0" y="138" width="1920" height="804"/></clipPath>
 <clipPath id="slice0"><polygon points="0,0 890,0 960,340 820,490 1020,610 0,1080"/></clipPath>
 <clipPath id="slice1"><polygon points="890,0 1920,0 1920,540 1020,610 820,490 960,340"/></clipPath>
 <clipPath id="slice2"><polygon points="0,1080 1020,610 1920,540 1920,1080"/></clipPath>
 </defs>
 <g clipPath="url(#film)">
 <rect width={W} height={H} fill={bg}/>
 <ellipse cx={1150} cy={530} rx={1000} ry={540} fill="url(#ambient)"/>
 {t<17.1&&<Intro t={t}/>}
 {t>=17&&t<30&&<Carry t={t}/>}
 {t>=30&&t<36.2&&<Wrong t={t}/>}
 {t>=36&&t<43.05&&<Fault t={t}/>}
 {t>=42.9&&t<70&&<System t={t}/>}
 {t>=70&&<End t={t}/>}
 <rect width={W} height={H} fill="url(#vignette)" pointerEvents="none"/>
 {/* A deterministic fine-grain emulsion, not a random glitch overlay. */}
 {Array.from({length:95},(_,i)=>{const x=hash(i+Math.floor(frame/2)*.77)*W,y=140+hash(i+9+Math.floor(frame/2)*.27)*800;return <circle key={i} cx={x} cy={y} r=".8" fill={WHITE} opacity=".08"/>})}
 <rect width={W} height={H} fill="#05080b" opacity={1-ramp(t,0,.6)}/>
 </g>
 <rect x="0" y="0" width={W} height="138" fill="#05080b"/>
 <rect x="0" y="942" width={W} height="138" fill="#05080b"/>
 </svg>
 </AbsoluteFill>
};
