"""Browser-document visits: new navigation starts fresh; reload reuses a visit."""
from uuid import uuid4
import streamlit as st
from streamlit.components.v2 import component
from src.storage import load_visit,save_visit


VISIT_JS="""
export default function(component) {
    const { data, setStateValue } = component;
    const storageKey = 'opc.travel.visit.v1';
    const freshId = () => typeof crypto.randomUUID === 'function' ? crypto.randomUUID() :
        Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
    if (!window.__opcTravelVisit) {
        const navigation = performance.getEntriesByType('navigation')[0]?.type || 'navigate';
        let prior = null;
        try { prior = JSON.parse(sessionStorage.getItem(storageKey)); } catch (_) {}
        const reuse = navigation === 'reload' && prior && typeof prior.id === 'string';
        window.__opcTravelVisit = {
            id: reuse ? prior.id : freshId(),
            navigation: navigation,
        };
        try { sessionStorage.setItem(storageKey, JSON.stringify(window.__opcTravelVisit)); } catch (_) {}
    }
    const visit = window.__opcTravelVisit;
    window.__opcVisitEmitter = setStateValue;
    if (!window.__opcVisitPageShow) {
        window.__opcVisitPageShow = true;
        window.addEventListener('pageshow', event => {
            if (event.persisted) {
                window.__opcTravelVisit = { id: freshId(), navigation: 'back_forward' };
                try { sessionStorage.setItem(storageKey, JSON.stringify(window.__opcTravelVisit)); } catch (_) {}
                window.__opcVisitEmitter('visit', window.__opcTravelVisit);
            }
        });
    }
    if (data.ack !== visit.id && window.__opcVisitSentTo !== data.mount) {
        window.__opcVisitSentTo = data.mount;
        setStateValue('visit', visit);
    }
}
"""
_browser_visit=component("opc_browser_visit",html="",js=VISIT_JS)


def initialize_visit():
    if "visit_id" not in st.session_state:
        st.session_state["visit_id"]=uuid4().hex
        st.session_state["visit_provisional"]=True
        st.session_state["visit_mount"]=uuid4().hex
    result=_browser_visit(key="browser_visit_detector",data={"mount":st.session_state["visit_mount"],
        "ack":None if st.session_state.get("visit_provisional") else st.session_state["visit_id"]},
        default={"visit":None},on_visit_change=lambda:None,height=0)
    event=result.visit
    if not isinstance(event,dict) or not isinstance(event.get("id"),str) or not 8<=len(event["id"])<=100:
        return
    if not st.session_state.get("visit_provisional") and st.session_state["visit_id"]==event["id"]:
        return
    previous=st.session_state["visit_id"]
    view=load_visit(event["id"])
    # Preserve any work explicitly submitted before the frontend handshake.
    provisional=load_visit(previous) if st.session_state.get("visit_provisional") else {}
    if provisional and not view:
        save_visit(event["id"],provisional)
        view=provisional
    st.session_state["visit_id"]=event["id"]
    st.session_state["visit_provisional"]=False
    for key in ("case","case_view_role","customer_case_id","owner_case_id","customer_supplier_order_id",
                "owner_supplier_order_id","supplier_selected","supplier_order_select_customer","supplier_order_select_owner","owner_history_select",
                "customer_work_area","owner_dashboard_page","owner_dashboard_order","owner_dashboard_return_page"):
        st.session_state.pop(key,None)
    st.session_state["identity"]=view.get("identity","客户")
    for key in ("customer_case_id","customer_supplier_order_id","owner_supplier_order_id","customer_work_area"):
        if key in view:
            st.session_state[key]=view[key]
    if view.get("work_area"):
        st.session_state["work_area"]=view["work_area"]
    else:
        st.session_state.pop("work_area", None)
    if view.get("customer_supplier_order_id"):
        st.session_state["supplier_selected"]=view["customer_supplier_order_id"]
        st.session_state["supplier_order_select_customer"]=view["customer_supplier_order_id"]
    if view.get("owner_supplier_order_id"):
        st.session_state["supplier_order_select_owner"]=view["owner_supplier_order_id"]


def remember_view(**changes):
    visit_id=st.session_state.get("visit_id")
    if visit_id:
        save_visit(visit_id,changes)
    for key,value in changes.items():
        if key not in {"identity","work_area"}:
            st.session_state[key]=value


def bind_case(case_id,owner=False):
    remember_view(**{("owner_case_id" if owner else "customer_case_id"):case_id})


def new_customer():
    remember_view(customer_case_id=None,customer_supplier_order_id=None)
    for key in list(st.session_state):
        if key in {"case","case_view_role","supplier_selected","supplier_created_pending","supplier_order_select_customer","supplier_wizard"} or key.startswith(("trip_","customer_","condition:","coupon:")):
            st.session_state.pop(key,None)


def resume_for_customer(case_id):
    new_customer()
    bind_case(case_id,False)
    st.session_state["identity"]="客户"
    st.session_state["work_area"]="团建策划与交付"
    remember_view(identity="客户",work_area="团建策划与交付")


def resume_supplier(order_id):
    remember_view(customer_supplier_order_id=order_id,identity="客户",work_area="酒店餐饮模拟预订")
    st.session_state["identity"]="客户"
    st.session_state["work_area"]="酒店餐饮模拟预订"
    st.session_state["supplier_selected"]=order_id
    st.session_state["supplier_order_select_customer"]=order_id


def new_supplier_request():
    remember_view(customer_supplier_order_id=None)
    for key in ("supplier_selected","supplier_created_pending","supplier_order_select_customer","supplier_wizard"):
        st.session_state.pop(key,None)
