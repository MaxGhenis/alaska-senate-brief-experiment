const bins=["dem_to_48.5","dem_48.5_49.0","dem_49.0_49.5","dem_49.5_50.0","dem_50.0_50.5","dem_50.5_51.0","dem_51.0_51.5","dem_over_51.5"];
const qs=["p05","p25","p50","p75","p95"];
const q=a=>Object.fromEntries(bins.map((b,i)=>[b,Object.fromEntries(qs.map((k,j)=>[k,a[i][j]]))]));
const p=a=>Object.fromEntries(bins.map((b,i)=>[b,{p:a[i]}]));
const forecasts={
dem_share:{all:{"le_48.5":.005,"le_49":.01,"le_49.5":.025,"le_50":.06,"le_50.5":.14,"le_51":.30,"le_51.5":.50}},
bm2_yes_share:q([[48.3,49.0,49.5,50.0,50.8],[47.8,48.4,48.8,49.2,49.9],[47.4,48.1,48.5,48.9,49.5],[47.1,47.7,48.1,48.5,49.1],[46.8,47.4,47.8,48.2,48.8],[46.4,47.0,47.4,47.8,48.4],[46.0,46.7,47.1,47.5,48.1],[45.0,45.9,46.4,46.9,47.5]]),
gov_dem_share:q([[51.7,52.8,53.3,53.8,54.5],[53.2,53.7,54.1,54.5,55.1],[53.6,54.2,54.6,55.0,55.6],[54.1,54.7,55.1,55.5,56.1],[54.6,55.2,55.6,56.1,63.9],[55.1,55.7,56.4,63.8,64.3],[55.6,63.5,63.9,64.3,64.8],[63.7,64.3,64.8,65.3,66.2]]),
recount:p([.06,.12,.25,.76,.89,.60,.34,.16]),
contest:p([.05,.08,.15,.34,.62,.42,.25,.12]),
court_order:p([.012,.018,.025,.045,.14,.09,.05,.025]),
winner_sworn:p([.996,.994,.990,.979,.958,.972,.984,.992]),
peltola_senator:p([.002,.002,.003,.004,.957,.971,.983,.991]),
dem_caucus:q(bins.map((b,i)=>i<4?[54,55,55,55,55]:[55,56,56,56,56])),
sullivan_post:p([.04,.04,.04,.04,.26,.26,.26,.26]),
ak_unemployment:q(bins.map(()=>[3.9,4.1,4.3,4.5,4.9])),
ak_oil:q(bins.map(()=>[455,487,506,523,548]))
};
const bounds={bm2_yes_share:[30,70],gov_dem_share:[30,70],dem_caucus:[35,65],ak_unemployment:[1,15],ak_oil:[200,700]};
const errors=[];for(const [name,fs]of Object.entries(forecasts)){if(name==="dem_share"){const a=Object.values(fs.all);if(a.some((v,i)=>v<0||v>1||i&&v<a[i-1]))errors.push(name);continue;}if(Object.keys(fs).length!==8)errors.push(name+" ranges");for(const f of Object.values(fs)){if("p" in f){if(f.p<0||f.p>1)errors.push(name);}else{const a=Object.values(f),[lo,hi]=bounds[name];if(a.length!==5||a.some((v,i)=>v<lo||v>hi||i&&v<a[i-1]||name==="dem_caucus"&&!Number.isInteger(v)))errors.push(name);}}}
store("forecasts",forecasts);text({nodes:Object.keys(forecasts).length,conditionalForecasts:88,errors});
