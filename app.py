import streamlit as st

def save_bill(emp, cust, btype, details, total):
    # fake save function
    print("Bill saved:", emp, cust, btype, details, total)

def update_item_stock(item, qty):
    # fake update function
    print("Stock updated:", item, qty)

emp_cid, cust_cid, btype = "E101", "C202", "ITEMS"
det, total = "Pen×2, Book×1", 250

with st.form("billing_form"):
    if st.form_submit_button("💾 Save Bill"):
        if not emp_cid or not cust_cid or total == 0:
            st.warning("Fill all fields.")
        else:
            save_bill(emp_cid, cust_cid, btype, det, total)

            # Deduct stock (example)
            update_item_stock("Pen", -2)
            update_item_stock("Book", -1)

            # Save in session
            st.session_state["last_bill"] = {
                "employee_cid": emp_cid,
                "customer_cid": cust_cid,
                "billing_type": btype,
                "details": det,
                "amount": total,
            }

            st.rerun()

# ✅ Show after rerun
if "last_bill" in st.session_state:
    lb = st.session_state["last_bill"]
    st.success("✅ Bill saved successfully!")
    st.markdown(
        f"""
        <div style="
          display:flex;
          align-items:center;
          border-left:3px solid #28a745;
          padding:10px;
          border-radius:8px;
          background:rgba(255,255,255,0.0);
          backdrop-filter: blur(6px);
          -webkit-backdrop-filter: blur(6px);
          margin-top:8px;
        ">
          <div style="flex:1">
            <div style="font-weight:700;font-size:16px;margin-bottom:4px">
              Saved bill — ₹{lb['amount']:.2f}
            </div>
            <div style="color:#FFFFFF;font-size:13px;margin-bottom:6px">
              Type: <strong>{lb['billing_type']}</strong> &nbsp;•&nbsp; Details: {lb['details']}
            </div>
            <div style="color:#666;font-size:12px">
              Seller CID: <code style="background:rgb(239 239 239 / 10%);padding:2px 6px;border-radius:4px">{lb['employee_cid']}</code>
              &nbsp;•&nbsp;
              Customer CID: <code style="background:rgb(239 239 239 / 10%);padding:2px 6px;border-radius:4px">{lb['customer_cid']}</code>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
