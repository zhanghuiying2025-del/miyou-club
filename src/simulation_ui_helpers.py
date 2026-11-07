import time
from uuid import uuid4
import streamlit as st
from src.runtime import submit_once,consume,active_jobs


def launch_job(entity_id,label,function,*args):
    st.session_state.pop("simulation_action_error",None)
    key=("simulation",entity_id,uuid4().hex)
    job=submit_once(key,entity_id,function,*args)
    st.session_state[f"simulation_job:{entity_id}"]=(job,label)


def job_status(entity_id):
    action_error=st.session_state.get("simulation_action_error")
    if action_error:
        st.error(action_error)
    pending=st.session_state.get(f"simulation_job:{entity_id}")
    if pending:
        job,label=pending
        if job.future.done():
            st.session_state.pop(f"simulation_job:{entity_id}",None)
            try:
                consume(job,lambda result:None)
                st.session_state.pop("simulation_action_error",None)
                st.session_state[f"simulation_notice:{entity_id}"]="任务完成，已保存模拟结果"
            except Exception as exc:
                st.session_state[f"simulation_notice:{entity_id}"]="任务未完成，可重试："+str(exc)[:500]
            st.rerun()
        st.status(f"{label} · 已等待{int(time.monotonic()-job.started)}秒",state="running")
        return True
    if active_jobs(entity_id):
        st.info("该记录有后台任务正在处理，完成后会显示保存的结果。")
        return True
    notice=st.session_state.get(f"simulation_notice:{entity_id}")
    if notice:
        st.info(notice)
    return False


def run_action(function,*args,**kwargs):
    try:
        result=function(*args,**kwargs)
    except (ValueError,RuntimeError) as exc:
        st.session_state["simulation_action_error"]=str(exc)
        st.error(str(exc))
        return None
    st.session_state.pop("simulation_action_error",None)
    st.rerun()
    return result
