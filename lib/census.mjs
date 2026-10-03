// ACS annotation codes are not numeric measurements. In particular, -555555555
// means a controlled estimate whose margin of error is not appropriate.
// https://www.census.gov/data/developers/data-sets/acs-1year/notes-on-acs-estimate-and-annotation-values.html
export function parseCountyPopulation(data) {
  const invalid=()=>new Error('Census returned a suppressed, unavailable, or invalid population estimate.');
  if(!Array.isArray(data)||!Array.isArray(data[0])||!Array.isArray(data[1]))throw invalid();
  const row=Object.fromEntries(data[0].map((key,i)=>[key,data[1][i]]));
  const numeric=value=>(typeof value==='number'||typeof value==='string'&&value.trim()!=='')&&Number.isSafeInteger(Number(value));
  if(typeof row.NAME!=='string'||!row.NAME.trim()||!numeric(row.B01003_001E)||Number(row.B01003_001E)<0||row.B01003_001EA)throw invalid();
  if(!numeric(row.B01003_001M))throw invalid();
  const population=Number(row.B01003_001E),moe=Number(row.B01003_001M);
  let marginOfError=null,marginOfErrorStatus,marginOfErrorNote;
  if(moe===-555555555){
    marginOfErrorStatus='controlled';
    marginOfErrorNote='Estimate controlled to an independent population estimate; a sampling margin of error is not appropriate.';
  }else if(moe>=0){
    marginOfError=moe;marginOfErrorStatus='available';marginOfErrorNote='Published ACS margin of error.';
  }else if([-222222222,-333333333,-666666666,-888888888,-999999999].includes(moe)){
    marginOfErrorStatus='unavailable';marginOfErrorNote='Census does not publish a numeric margin of error for this estimate.';
  }else throw invalid();
  return {name:row.NAME,population,marginOfError,marginOfErrorStatus,marginOfErrorNote,vintage:'2024 ACS 5-year',note:'Entire county estimate, not population exposed to a hazard.',url:'https://api.census.gov/data/2024/acs/acs5.html'};
}
