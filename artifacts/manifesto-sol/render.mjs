import {mkdirSync,writeFileSync,existsSync,renameSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import path from 'node:path';
// Every scratch path belongs to this project. A persisted frame pass survives
// a failed mux, and the exported film is renamed into place only on success.
const scratch=path.resolve('out/render-tmp');
mkdirSync(scratch,{recursive:true});
process.env.TMPDIR=scratch;
const {bundle}=await import('@remotion/bundler');
const {openBrowser,renderStill,renderFrames,selectComposition}=await import('@remotion/renderer');
const args=process.argv.slice(2);
const out=path.resolve(args.includes('--out')?args[args.indexOf('--out')+1]:'out');
mkdirSync(out,{recursive:true});
const serveUrl=await bundle({entryPoint:path.resolve('src/index.tsx'),outDir:path.resolve('build')});
const browser=await openBrowser('chrome');
const composition=await selectComposition({serveUrl,id:'Continuity',puppeteerInstance:browser});
if(args.includes('--stills')){
 const times=[2,7,10.5,14.7,20.5,27,32.5,35,38,40.5,45,50,56,62,66,71.5,76,78.5,81];
 for(const t of times){await renderStill({composition,serveUrl,frame:Math.round(t*30),output:path.join(out,`frame-${t}.jpg`),imageFormat:'jpeg',jpegQuality:94,puppeteerInstance:browser,scale:0.75});console.log(`still ${t}s`);}
}else{
 const framesDir=path.resolve('out/frames');
 mkdirSync(framesDir,{recursive:true});
 const frameFile=f=>path.join(framesDir,`frame-${String(f).padStart(6,'0')}.jpg`);
 const missing=Array.from({length:composition.durationInFrames},(_,i)=>i).filter(f=>!existsSync(frameFile(f)));
 let last=-1;
 if(missing.length){
  await renderFrames({composition,serveUrl,inputProps:{},outputDir:null,frames:missing,
   imageFormat:'jpeg',jpegQuality:94,concurrency:2,muted:true,puppeteerInstance:browser,
   onStart:({frameCount})=>console.log(`rendering ${frameCount} missing frames; concurrency 2`),
   onFrameBuffer:(buffer,frame)=>writeFileSync(frameFile(frame),buffer),
   onFrameUpdate:(count,index)=>{const n=Math.floor(count/missing.length*10)*10;if(n>last){last=n;console.log(`frames ${n}% (last ${index})`);}}
  });
 }
 await browser.close({silent:true});
 const temp=path.join(out,'brnrd-continuity-sol.partial.mp4');
 const encoded=spawnSync('ffmpeg',['-y','-hide_banner','-framerate','30','-start_number','0','-i',path.join(framesDir,'frame-%06d.jpg'),
  '-i',path.resolve('public/score.wav'),'-map','0:v:0','-map','1:a:0','-frames:v','2520','-t','84',
  '-vf','scale=in_range=full:out_range=tv,format=yuv420p','-c:v','libx264','-crf','17','-preset','medium',
  '-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709','-c:a','aac','-b:a','320k','-ar','48000','-movflags','+faststart',temp],{stdio:'inherit'});
 if(encoded.status!==0)throw new Error(`FFmpeg mux failed: ${encoded.status}; the completed frames are preserved in ${framesDir}`);
 renameSync(temp,path.join(out,'brnrd-continuity-sol.mp4'));
 writeFileSync(path.join(out,'render-complete.txt'),'84 seconds / 2520 frames / 1920x1080 / 30 fps\n');
 console.log('complete');
 process.exit(0);
}
await browser.close({silent:true});
console.log('stills complete');
