// ============================================================================
// windcore.js  —  Diagnostic terrain wind model (browser-tractable)
//
// Given an elevation grid + a single ambient wind vector (speed + direction),
// produce gridded near-surface wind and the fields a glider pilot cares about:
//   * speed-up   (acceleration over crests / through gaps)
//   * lift       (orographic upslope lift on windward faces)   w = V·∇z
//   * sink       (lee-side downslope sink)
//   * rotor      (empirical lee-turbulence hazard, Röckle-style wake)
//   * convergence(horizontal flow piling up -> rising air / thermals)
//
// Method: mass-consistent diagnostic adjustment (the approach USFS WindNinja
// uses for its conservation-of-mass solver). We solve a variable-coefficient
// Poisson equation for a velocity potential phi so that the depth-integrated
// flow conserves mass in a terrain-following layer:
//     V = Va + grad(phi),   div( D * V ) = 0,   D = layer depth above ground
//  => div( D grad phi ) = -div(D Va) = -Va · grad(D)      (Va constant)
// Solved with SOR. This is diagnostic (no separation physics); the lee rotor
// is added empirically. True separation/rotor = the later CFD backend phase.
//
// Pure functions, no DOM — shared by the app and the Node tests.
// ============================================================================

// compass "from" direction (0=N,90=E) + speed -> velocity the air blows TOWARD
// x = east, y = north
function windToVector(speed, fromDeg){
  const r = fromDeg*Math.PI/180;
  return { ux: -speed*Math.sin(r), vy: -speed*Math.cos(r) };
}

function grad(field, w, h, dx){
  const gx=new Float32Array(w*h), gy=new Float32Array(w*h);
  const at=(r,c)=>field[Math.max(0,Math.min(h-1,r))*w+Math.max(0,Math.min(w-1,c))];
  for(let r=0;r<h;r++)for(let c=0;c<w;c++){
    gx[r*w+c]=(at(r,c+1)-at(r,c-1))/(2*dx);
    gy[r*w+c]=(at(r+1,c)-at(r-1,c))/(2*dx);   // +gy = toward +north? rows increase south
  }
  return {gx,gy};
}

// Solve div(D grad phi) = rhs with SOR. Dirichlet phi=0 on the boundary (lets
// flow enter/leave the domain). Df* are face conductivities.
function solvePhi(rhs, D, w, h, dx, iters, omega){
  const phi=new Float32Array(w*h);
  const idx=(r,c)=>r*w+c;
  const dx2=dx*dx;
  omega=omega||1.7; iters=iters||300;
  for(let it=0; it<iters; it++){
    for(let r=1;r<h-1;r++){
      for(let c=1;c<w-1;c++){
        const k=idx(r,c);
        const De=0.5*(D[k]+D[k+1]),   Dw=0.5*(D[k]+D[k-1]);
        const Dn=0.5*(D[k]+D[k-w]),   Ds=0.5*(D[k]+D[k+w]);
        const sumD=De+Dw+Dn+Ds;
        const nb=De*phi[k+1]+Dw*phi[k-1]+Dn*phi[k-w]+Ds*phi[k+w];
        const newv=(nb - rhs[k]*dx2)/sumD;
        phi[k]=phi[k]+omega*(newv-phi[k]);
      }
    }
  }
  return phi;
}

// Main solver.  z: Float array (meters), w,h, dx (m), speed (m/s), fromDeg.
// opts.layer = boundary-layer depth (m). Returns gridded fields.
function diagnosticWind(z, w, h, dx, speed, fromDeg, opts){
  opts=opts||{};
  const layer=opts.layer||800;        // terrain-following layer depth (m)
  const iters=opts.iters||300;
  // grid convention: +x=east(col+), +y=south(row+). Wind FROM fromDeg.
  const _a=fromDeg*Math.PI/180, _sp=Math.max(0.1,speed);
  const ux=-_sp*Math.sin(_a), vy=_sp*Math.cos(_a);

  // layer depth above ground; flow squeezes where terrain is high
  let zmax=-1e9; for(let i=0;i<w*h;i++) if(z[i]>zmax) zmax=z[i];
  const top=zmax+layer;
  const D=new Float32Array(w*h);
  for(let i=0;i<w*h;i++) D[i]=Math.max(layer*0.15, top - z[i]);

  // rhs = -Va·grad(D)
  const gD=grad(D,w,h,dx);
  const rhs=new Float32Array(w*h);
  for(let i=0;i<w*h;i++) rhs[i]=-(ux*gD.gx[i]+vy*gD.gy[i]);

  const phi=solvePhi(rhs,D,w,h,dx,iters,opts.omega);

  // V = Va + grad(phi)
  const gP=grad(phi,w,h,dx);
  const Vx=new Float32Array(w*h), Vy=new Float32Array(w*h), spd=new Float32Array(w*h);
  for(let i=0;i<w*h;i++){ Vx[i]=ux+gP.gx[i]; Vy[i]=vy+gP.gy[i]; spd[i]=Math.hypot(Vx[i],Vy[i]); }

  // orographic vertical velocity w = V·grad(z); +up on windward, -down on lee
  const gz=grad(z,w,h,dx);
  const wvel=new Float32Array(w*h);
  for(let i=0;i<w*h;i++) wvel[i]=Vx[i]*gz.gx[i]+Vy[i]*gz.gy[i];

  // horizontal divergence -> convergence = max(0,-div)
  const conv=new Float32Array(w*h);
  const at=(f,r,c)=>f[Math.max(0,Math.min(h-1,r))*w+Math.max(0,Math.min(w-1,c))];
  for(let r=0;r<h;r++)for(let c=0;c<w;c++){
    const div=(at(Vx,r,c+1)-at(Vx,r,c-1))/(2*dx)+(at(Vy,r+1,c)-at(Vy,r-1,c))/(2*dx);
    conv[r*w+c]=Math.max(0,-div);
  }

  // empirical lee-rotor hazard: march UPWIND; if higher terrain is found within
  // a wake length and the air here is sinking (lee), flag rotor ~ crest drop.
  const ang=fromDeg*Math.PI/180;
  const upx=Math.sin(ang), upy=-Math.cos(ang);    // step toward wind source (upwind), +y=south
  const Lmax=opts.wake||1500;                      // wake search length (m)
  const steps=Math.max(4,Math.round(Lmax/dx));
  const rotor=new Float32Array(w*h);
  for(let r=0;r<h;r++)for(let c=0;c<w;c++){
    const k=r*w+c;
    if(wvel[k]>=0){ rotor[k]=0; continue; }         // only lee (sinking) cells
    let crest=z[k], hitDist=0;
    for(let s=1;s<=steps;s++){
      const rr=Math.round(r+upy*s), cc=Math.round(c+upx*s);
      if(rr<0||cc<0||rr>=h||cc>=w) break;
      const zv=z[rr*w+cc];
      if(zv>crest){ crest=zv; hitDist=s*dx; }
    }
    const drop=crest - z[k];
    if(drop>15){                                    // must be a real ridge upwind
      const prox=Math.exp(-hitDist/Math.max(dx,Lmax*0.5));
      rotor[k]=drop*prox*Math.min(1,(-wvel[k]));    // scale by drop, proximity, sink strength
    }
  }

  return {Vx,Vy,spd,wvel,conv,rotor,ambient:Math.hypot(ux,vy)};
}

if(typeof module!=='undefined') module.exports={windToVector,diagnosticWind};

// ----------------------------- SELF TEST -----------------------------
if(typeof require!=='undefined' && require.main===module){
  const W=100,H=100,dx=30;
  const z=new Float32Array(W*H);
  // isolated Gaussian hill, 250 m high, centered
  for(let r=0;r<H;r++)for(let c=0;c<W;c++){
    z[r*W+c]=250*Math.exp(-(((c-50)**2+(r-50)**2)/(2*14**2)));
  }
  const speed=10; // m/s
  const fromDeg=270; // wind FROM west -> blows toward east (+x)
  const F=diagnosticWind(z,W,H,dx,speed,fromDeg,{layer:700,iters:400});

  const at=(f,r,c)=>f[r*W+c];
  const crest=at(F.spd,50,50);
  const upwindFace=at(F.wvel,50,42);   // west (windward) flank
  const leeFace   =at(F.wvel,50,58);   // east (lee) flank
  const farfield  =at(F.spd,50,5);
  const rotorLee  =at(F.rotor,50,60);
  const rotorWind =at(F.rotor,50,40);
  let convMax=0, spdMax=0; for(let i=0;i<W*H;i++){if(F.conv[i]>convMax)convMax=F.conv[i];if(F.spd[i]>spdMax)spdMax=F.spd[i];}

  console.log('ambient |Va|       :', F.ambient.toFixed(2),'m/s');
  console.log('far-field speed    :', farfield.toFixed(2),'(expect ~ambient)');
  console.log('crest speed        :', crest.toFixed(2),'(expect > ambient: speed-up)');
  console.log('windward w (V·∇z)  :', upwindFace.toFixed(3),'(expect > 0: lift)');
  console.log('lee w (V·∇z)       :', leeFace.toFixed(3),'(expect < 0: sink)');
  console.log('rotor hazard lee   :', rotorLee.toFixed(2),'(expect > 0)');
  console.log('rotor hazard wind  :', rotorWind.toFixed(2),'(expect ~0)');
  console.log('convergence max    :', convMax.toFixed(4));

  // NORTH wind (from=0 -> blows toward +row/south). Windward = NORTH flank (row<50),
  // lee = SOUTH flank (row>50). This case catches the row-direction sign bug.
  const N=diagnosticWind(z,W,H,dx,speed,0,{layer:700,iters:400});
  const nWindward=at(N.wvel,42,50);  // north flank
  const nLee     =at(N.wvel,58,50);  // south flank
  const nRotorLee=at(N.rotor,60,50); // south (lee)
  const nRotorWind=at(N.rotor,40,50);// north (windward)
  console.log('--- north wind ---');
  console.log('windward(N) w       :', nWindward.toFixed(3),'(expect > 0: lift)');
  console.log('lee(S) w            :', nLee.toFixed(3),'(expect < 0: sink)');
  console.log('rotor lee(S)        :', nRotorLee.toFixed(2),'(expect > 0)');
  console.log('rotor windward(N)   :', nRotorWind.toFixed(2),'(expect ~0)');

  const pass = farfield>0.7*F.ambient && farfield<1.3*F.ambient
    && crest>1.1*F.ambient
    && upwindFace>0 && leeFace<0
    && rotorLee>rotorWind && rotorWind<1e-3
    && convMax>0 && spdMax<5*F.ambient
    && nWindward>0 && nLee<0 && nRotorLee>nRotorWind && nRotorWind<1e-3;
  console.log('\nRESULT:', pass?'PHYSICS OK ✅':'CHECK ❌');
  process.exit(pass?0:1);
}
