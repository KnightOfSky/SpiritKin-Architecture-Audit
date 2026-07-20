// SpiritKin Desktop Console — shared API layer.
// Extracted from desktop_console.html. Loaded as a classic script before the
// inline app script, so these functions share the global scope and resolve
// apiBase()/authHeaders() (defined in the inline script) at call time.

function desktopUrl(path){return `${apiBase()}/desktop/${path}`}
function commandUrl(){return `${apiBase()}/command`}
function desktopStateUrl(){return desktopUrl("state")}
function ecosystemReviewUrl(){return desktopUrl("ecosystem-review")}
function moduleManagementUrl(){return desktopUrl("module-management")}
function servicesUrl(){return desktopUrl("services")}
function servicePortsUrl(){return desktopUrl("service-ports")}
function diagnosticsUrl(){return desktopUrl("diagnostics")}
function logsUrl(logId=""){return desktopUrl(`logs${logId?`?log_id=${encodeURIComponent(logId)}`:""}`)}
function actionLogUrl(limit=80){return desktopUrl(`action-log?limit=${encodeURIComponent(limit)}`)}
function contextUrl(){return desktopUrl("context")}
function projectOverviewUrl(){return desktopUrl("project-overview")}
function projectRuntimeUrl(){return desktopUrl("project-runtime")}
function searchManagementUrl(){return desktopUrl("search-management")}
function evolutionUrl(){return desktopUrl("evolution")}
function workflowsUrl(){return desktopUrl("workflows")}
function learningUrl(){return desktopUrl("learning")}
function modelCatalogUrl(){return desktopUrl("model-catalog")}
function agentManagementUrl(){return desktopUrl("agent-management")}
function skillsUrl(){return desktopUrl("skills")}
function knowledgeBaseUrl(){return desktopUrl("knowledge-base")}
function mcpManagementUrl(){return desktopUrl("mcp-management")}
function mobileManagementUrl(){return desktopUrl("mobile-management")}
function stateMaintenanceUrl(){return desktopUrl("state-maintenance")}
function safetyUrl(){return desktopUrl("safety")}

async function fetchJson(url,options={}){
 const r=await fetch(url,options);
 const d=r.status===204?{ok:true}:await r.json();
 if(!r.ok||!d.ok)throw new Error(d.detail||d.error||`HTTP ${r.status}`);
 return d;
}
function postOptions(payload){return {method:"POST",headers:authHeaders({"Content-Type":"application/json"}),body:JSON.stringify(payload)}}
