"""NORM03B R22: locate immutable PRIOR successful GitHub Actions state.

No write API, credentials never printed. Artifact bytes are downloaded by the
official actions/download-artifact action, not this script.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote

ARTIFACT_NAME = "NORM03B_R22_FUNDING_STATE"
WORKFLOW_FILENAME = "norm-03b-r22-funding.yml"


def get_json(url, token):
    if not (url.startswith("https://api.github.com/repos/") and
            "/actions/" in url):
        raise ValueError("GitHub Actions read-only API only")
    req=Request(url,method="GET",headers={
        "Accept":"application/vnd.github+json",
        "Authorization":f"Bearer {token}",
        "X-GitHub-Api-Version":"2022-11-28",
        "User-Agent":"NORM03B-R22-READONLY"
    })
    with urlopen(req,timeout=12) as response:
        if response.status!=200:
            raise ValueError("GitHub API status not OK")
        payload=response.read(2_000_001)
    if len(payload)>2_000_000:
        raise ValueError("oversized GitHub API response")
    return json.loads(payload)


def choose_previous(repo, current_id, workflow_runs, artifact_by_run):
    """Do not allow self-replay, future or failed runs to seed evidence."""
    candidates=[]
    older_successes=0
    for r in workflow_runs:
        try:
            rid=int(r["id"])
            if rid>=current_id or r.get("status")!="completed" or r.get("conclusion")!="success":
                continue
            older_successes+=1
            valid=[a for a in artifact_by_run.get(rid,[]) if
                   a.get("name")==ARTIFACT_NAME and not a.get("expired",False)
                   and int(a.get("size_in_bytes",0))>0]
            if valid:
                candidates.append((rid,max(valid,key=lambda a:a.get("created_at",""))))
        except (ValueError,KeyError,TypeError):
            continue
    if candidates:
        rid,a=max(candidates,key=lambda z:z[0])
        return {"status":"PREVIOUS_PROVEN_STATE_FOUND","previous_run_id":rid,
                "previous_artifact_id":int(a["id"]),
                "older_successful_runs":older_successes}
    return {"status":("FIRST_RUN_NO_HISTORY" if older_successes==0
                       else "PREVIOUS_SUCCESS_WITHOUT_VALID_ARTIFACT_FAIL_CLOSED"),
            "previous_run_id":None,"previous_artifact_id":None,
            "older_successful_runs":older_successes}


def discover(repo, current_id, token):
    if "/" not in repo or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_/." for c in repo):
        raise ValueError("invalid repo")
    if current_id<=0:
        raise ValueError("invalid current run ID")
    path=f"https://api.github.com/repos/{repo}/actions/workflows/{quote(WORKFLOW_FILENAME)}/runs?per_page=30"
    response=get_json(path,token)
    runs=response.get("workflow_runs",[])
    if not isinstance(runs,list):
        raise ValueError("nonlist workflow runs")
    prior_ids=[int(r["id"]) for r in runs if
               int(r.get("id",0))<current_id and
               r.get("status")=="completed" and
               r.get("conclusion")=="success"]
    artifacts={}
    for rid in prior_ids[:12]:
        url=f"https://api.github.com/repos/{repo}/actions/runs/{rid}/artifacts?per_page=100"
        data=get_json(url,token)
        artifacts[rid]=data.get("artifacts",[])
        if any(a.get("name")==ARTIFACT_NAME and not a.get("expired",False)
               for a in artifacts[rid]):
            break
    return choose_previous(repo,current_id,runs,artifacts)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--github-output",type=Path,required=True)
    args=parser.parse_args()
    token=os.getenv("GH_TOKEN","")
    repository=os.getenv("GITHUB_REPOSITORY","")
    current=int(os.getenv("GITHUB_RUN_ID","0"))
    if not token or not repository or not current:
        raise RuntimeError("GitHub Actions token/repo/run context required")
    choice=discover(repository,current,token)
    if choice["status"]=="PREVIOUS_SUCCESS_WITHOUT_VALID_ARTIFACT_FAIL_CLOSED":
        raise RuntimeError("previous successful R22 run has no preserved state")
    args.github_output.parent.mkdir(parents=True,exist_ok=True)
    with args.github_output.open("a",encoding="utf-8") as fp:
        fp.write(f"previous_run_id={choice['previous_run_id'] or ''}\n")
        fp.write(f"previous_artifact_id={choice['previous_artifact_id'] or ''}\n")
        fp.write(f"chain_status={choice['status']}\n")
    print(json.dumps(choice,sort_keys=True))


if __name__=="__main__":
    main()
