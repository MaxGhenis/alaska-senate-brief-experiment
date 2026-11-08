const ranges=["dem_to_48.5","dem_48.5_49.0","dem_49.0_49.5","dem_49.5_50.0","dem_50.0_50.5","dem_50.5_51.0","dem_51.0_51.5","dem_over_51.5"];
const qkeys=["p05","p25","p50","p75","p95"];
const fq=rows=>Object.fromEntries(ranges.map((r,i)=>[r,Object.fromEntries(qkeys.map((k,j)=>[k,rows[i][j]]))]));
const fp=ps=>Object.fromEntries(ranges.map((r,i)=>[r,{p:ps[i]}]));
const forecasts={
dem_share:{all:{le_48_5:.005,le_49:.012,le_49_5:.025,le_50:.05,le_50_5:.12,le_51:.26,le_51_5:.47}},
bm2_yes_share:fq([[47.9,48.6,49.1,49.6,50.4],[47.5,48.1,48.6,49.1,49.7],[47.2,47.9,48.3,48.8,49.4],[47,47.6,48,48.5,49.1],[46.7,47.3,47.8,48.2,48.8],[46.4,47.1,47.5,47.9,48.6],[46.2,46.8,47.2,47.7,48.3],[45.5,46.2,46.7,47.2,47.9]]),
gov_dem_share:fq([[51.7,52.6,53.2,53.7,54.5],[52.9,53.5,54,54.4,55.1],[53.3,54,54.5,54.9,55.6],[53.8,54.5,54.9,55.4,63.3],[54.3,54.9,55.4,56.1,64],[54.7,55.4,56.4,63.8,64.4],[55.2,63.2,63.8,64.2,64.7],[63.3,64,64.4,64.8,65.5]]),
recount:fp([.06,.13,.32,.84,.94,.62,.32,.15]),
contest:fp([.04,.07,.13,.28,.60,.43,.22,.10]),
court_order:fp([.008,.012,.022,.055,.17,.11,.055,.025]),
winner_sworn:fp([.996,.995,.993,.987,.965,.978,.988,.993]),
peltola_senator:fp([.0002,.0003,.0005,.001,.964,.977,.987,.992]),
dem_caucus:fq(ranges.map((_,i)=>i<4?[54,54,55,55,55]:[55,55,56,56,56])),
sullivan_post:fp([.03,.03,.035,.04,.24,.24,.23,.22]),
ak_unemployment:fq(ranges.map(()=>[4,4.2,4.3,4.4,4.7])),
ak_oil:fq(ranges.map(()=>[455,495,515,532,550]))
};
const bounds={bm2_yes_share:[30,70],gov_dem_share:[30,70],dem_caucus:[35,65],ak_unemployment:[1,15],ak_oil:[200,700]};
for(const [n,fs] of Object.entries(forecasts)){
 if(n==="dem_share"){const vs=Object.values(fs.all);if(vs.some((x,i)=>x<0||x>1||(i&&x<vs[i-1])))throw Error(n);continue;}
 if(Object.keys(fs).join()!==ranges.join())throw Error("ranges "+n);
 for(const [r,q]of Object.entries(fs)){
  let vs=Object.values(q);
  if(vs.some((x,i)=>!Number.isFinite(x)||(i&&x<vs[i-1])))throw Error("monotonic "+n+" "+r);
  if(bounds[n]){if(vs.some(x=>x<bounds[n][0]||x>bounds[n][1]))throw Error("bound "+n);if(n==="dem_caucus"&&vs.some(x=>!Number.isInteger(x)))throw Error("integer");}
  else if(vs.some(x=>x<0||x>1))throw Error("probability "+n);
 }
}
store("final_forecasts",forecasts);
text({validation:"All 12 nodes have the required forecasts; all probabilities, quantity bounds, integer counts, and ordered quantiles pass.",prior_context:load("prior_context"),scenario_count:8});
