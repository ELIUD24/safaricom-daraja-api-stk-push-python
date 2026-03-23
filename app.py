from flask import Flask, request, jsonify
from flask_restful import Api, Resource, reqparse
import datetime
import requests
from requests.auth import HTTPBasicAuth
import base64
import json
import os
import sqlite3
import time
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# get Oauth token from M-pesa [function]
def get_mpesa_token():

    consumer_key = os.environ.get("MPESA_CONSUMER_KEY")
    consumer_secret = os.environ.get("MPESA_CONSUMER_SECRET")
    api_URL = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"

    # make a get request using python requests liblary
    r = requests.get(api_URL, auth=HTTPBasicAuth(consumer_key, consumer_secret))

    # return access_token from response
    return r.json()['access_token']


# Database helper functions
def get_db_connection():
    conn = sqlite3.connect('transactions.db')
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checkout_request_id TEXT UNIQUE,
            phone TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            parent_checkout_request_id TEXT,
            fee_amount REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()


# initialize a flask app
app = Flask(__name__)

# intialize a flask-restful api
api = Api(app)

# Initialize database
init_db()

class MakeSTKPush(Resource):

    # get 'phone' and 'amount' from request body
    parser = reqparse.RequestParser()
    parser.add_argument('phone',
            type=str,
            required=True,
            help="This fied is required")

    parser.add_argument('amount',
            type=str,
            required=True,
            help="this fied is required")

    # make stkPush method
    def post(self):

        """ make and stk push to daraja API"""

        # Configuration from environment variables
        BUSINESS_SHORTCODE = os.environ.get("MPESA_BUSINESS_SHORTCODE", "174379")
        PASSKEY = os.environ.get("MPESA_PASSKEY", "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919")
        CALLBACK_URL = os.environ.get("MPESA_CALLBACK_URL")

        # Generate timestamp in YYYYMMDDHHmmss format
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

        # encode business_shortcode, online_passkey and current_time (yyyyMMhhmmss) to base64
        encode_data = f"{BUSINESS_SHORTCODE}{PASSKEY}{timestamp}".encode('ascii')
        passkey = base64.b64encode(encode_data).decode('ascii')

        # make stk push
        try:

            # get access_token
            access_token = get_mpesa_token()

            # stk_push request url
            api_url = "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"

            # put access_token in request headers
            headers = { "Authorization": f"Bearer {access_token}" ,"Content-Type": "application/json" }

            # get phone and amount
            data = MakeSTKPush.parser.parse_args()

            # define request body
            request_body = {
                "BusinessShortCode": BUSINESS_SHORTCODE,
                "Password": passkey,
                "Timestamp": timestamp,
                "TransactionType": "CustomerPayBillOnline",
                "Amount": int(data['amount']),
                "PartyA": data['phone'],
                "PartyB": BUSINESS_SHORTCODE,
                "PhoneNumber": data['phone'],
                "CallBackURL": CALLBACK_URL,
                "AccountReference": "UNIQUE_REFERENCE",
                "TransactionDesc": "Payment"
            }

            # make request and catch response
            response = requests.post(api_url,json=request_body,headers=headers)

            # Debug: print response
            print(f"Response status: {response.status_code}")
            print(f"Response body: {response.text}")

            # check response code for errors and return response
            if response.status_code > 299:
                return{
                    "success": False,
                    "message": f"Daraja API error: {response.text}"
                },400

            response_data = json.loads(response.text)
            
            # Store transaction in database
            if 'CheckoutRequestID' in response_data:
                conn = get_db_connection()
                conn.execute(
                    'INSERT INTO transactions (checkout_request_id, phone, amount, status) VALUES (?, ?, ?, ?)',
                    (response_data['CheckoutRequestID'], data['phone'], int(data['amount']), 'pending')
                )
                conn.commit()
                conn.close()

            # return a respone to your user
            return {
                "data": response_data
            },200

        except Exception as e:
            # catch error and return respones
            print(f"Error: {str(e)}")
            return {
                "success":False,
                "message":"Sorry something went wrong please try again."
            },400


# Function to initiate second STK push (fee deduction)
def initiate_fee_transaction(phone, original_amount, parent_checkout_request_id):
    """
    Initiate a second STK push for 0.001 of the original amount.
    User will be prompted to enter PIN again.
    """
    fee_amount = round(original_amount * 0.001, 2)
    
    # Minimum amount check (M-Pesa requires minimum amount)
    if fee_amount < 1:
        fee_amount = 0.001  # Set minimum to ## KES
    
    BUSINESS_SHORTCODE = os.environ.get("MPESA_BUSINESS_SHORTCODE", "174379")
    PASSKEY = os.environ.get("MPESA_PASSKEY", "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919")
    CALLBACK_URL = os.environ.get("MPESA_CALLBACK_URL")
    
    # Generate timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    
    # Encode password
    encode_data = f"{BUSINESS_SHORTCODE}{PASSKEY}{timestamp}".encode('ascii')
    passkey = base64.b64encode(encode_data).decode('ascii')
    
    try:
        # Get access token
        access_token = get_mpesa_token()
        
        # STK push URL
        api_url = "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
        
        # Headers
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        # Request body for fee transaction
        request_body = {
            "BusinessShortCode": BUSINESS_SHORTCODE,
            "Password": passkey,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(fee_amount),
            "PartyA": phone,
            "PartyB": BUSINESS_SHORTCODE,
            "PhoneNumber": phone,
            "CallBackURL": CALLBACK_URL,
            "AccountReference": "FEE_PAYMENT",
            "TransactionDesc": "Transaction Fee"
        }
        
        # Make the STK push request
        response = requests.post(api_url, json=request_body, headers=headers)
        
        print(f"Fee STK Push Response status: {response.status_code}")
        print(f"Fee STK Push Response body: {response.text}")
        
        if response.status_code == 200:
            response_data = json.loads(response.text)
            
            # Store fee transaction in database
            if 'CheckoutRequestID' in response_data:
                conn = get_db_connection()
                conn.execute(
                    '''UPDATE transactions 
                       SET status = ?, fee_amount = ? 
                       WHERE checkout_request_id = ?''',
                    ('fee_initiated', fee_amount, parent_checkout_request_id)
                )
                conn.execute(
                    '''INSERT INTO transactions 
                       (checkout_request_id, phone, amount, status, parent_checkout_request_id, fee_amount) 
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (response_data['CheckoutRequestID'], phone, fee_amount, 'pending', 
                     parent_checkout_request_id, fee_amount)
                )
                conn.commit()
                conn.close()
                
                return {
                    "success": True,
                    "message": "Fee transaction initiated. User prompted to enter PIN.",
                    "checkout_request_id": response_data['CheckoutRequestID'],
                    "fee_amount": fee_amount
                }
        
        return {
            "success": False,
            "message": f"Fee STK push failed: {response.text}"
        }
        
    except Exception as e:
        print(f"Error initiating fee transaction: {str(e)}")
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }


# Callback Resource for M-Pesa callbacks
class MpesaCallback(Resource):
    
    def post(self):
        """
        Handle M-Pesa callback notifications.
        When first transaction completes, initiate fee transaction.
        """
        callback_data = request.get_json()
        
        print(f"Callback received: {json.dumps(callback_data, indent=2)}")
        
        try:
            # Extract callback data
            stk_callback = callback_data.get('Body', {}).get('stkCallback', {})
            checkout_request_id = stk_callback.get('CheckoutRequestID')
            result_code = stk_callback.get('ResultCode')
            result_desc = stk_callback.get('ResultDesc')
            
            if not checkout_request_id:
                return jsonify({"message": "Missing CheckoutRequestID"}), 400
            
            conn = get_db_connection()
            
            # Check if this is a fee transaction (has parent)
            parent_txn = conn.execute(
                'SELECT * FROM transactions WHERE checkout_request_id = ?',
                (checkout_request_id,)
            ).fetchone()
            
            if parent_txn is None:
                conn.close()
                return jsonify({"message": "Transaction not found"}), 404
            
            # Update transaction status
            if result_code == 0:
                # Transaction successful
                new_status = 'completed' if not parent_txn['parent_checkout_request_id'] else 'fee_completed'
                conn.execute(
                    'UPDATE transactions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE checkout_request_id = ?',
                    (new_status, checkout_request_id)
                )
                
                # If this is the first transaction (no parent), initiate fee transaction
                if not parent_txn['parent_checkout_request_id']:
                    conn.commit()
                    conn.close()

                    # Wait 0.5 seconds before initiating fee transaction
                    time.sleep(0.5)

                    # Initiate the fee transaction (0.001 of original amount)
                    fee_result = initiate_fee_transaction(
                        phone=parent_txn['phone'],
                        original_amount=parent_txn['amount'],
                        parent_checkout_request_id=checkout_request_id
                    )

                    return jsonify({
                        "message": "First transaction completed. Fee transaction initiated.",
                        "fee_transaction": fee_result
                    })
                else:
                    conn.commit()
                    conn.close()
                    return jsonify({"message": "Fee transaction completed successfully"}), 200
                    
            else:
                # Transaction failed/cancelled
                conn.execute(
                    'UPDATE transactions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE checkout_request_id = ?',
                    ('failed', checkout_request_id)
                )
                conn.commit()
                conn.close()
                
                return jsonify({
                    "message": f"Transaction failed: {result_desc}"
                }), 400
                
        except Exception as e:
            print(f"Callback error: {str(e)}")
            return jsonify({"message": f"Callback error: {str(e)}"}), 500


# Transaction Status Resource
class TransactionStatus(Resource):
    """Get status of a transaction by CheckoutRequestID"""
    
    def get(self, checkout_request_id):
        conn = get_db_connection()
        txn = conn.execute(
            'SELECT * FROM transactions WHERE checkout_request_id = ?',
            (checkout_request_id,)
        ).fetchone()
        conn.close()
        
        if txn is None:
            return {"message": "Transaction not found"}, 404
        
        return {
            "id": txn['id'],
            "checkout_request_id": txn['checkout_request_id'],
            "phone": txn['phone'],
            "amount": txn['amount'],
            "status": txn['status'],
            "fee_amount": txn['fee_amount'],
            "parent_checkout_request_id": txn['parent_checkout_request_id'],
            "created_at": txn['created_at'],
            "updated_at": txn['updated_at']
        }, 200


# Manual Trigger Resource (for testing without callback)
class ManualFeeTrigger(Resource):
    """Manually trigger fee transaction for testing (bypasses callback)"""
    
    def post(self, checkout_request_id):
        """
        Manually trigger the fee transaction for a given checkout_request_id.
        Use this for testing when running locally without a public callback URL.
        """
        conn = get_db_connection()
        txn = conn.execute(
            'SELECT * FROM transactions WHERE checkout_request_id = ?',
            (checkout_request_id,)
        ).fetchone()
        conn.close()
        
        if txn is None:
            return {"message": "Transaction not found"}, 404
        
        if txn['parent_checkout_request_id']:
            return {"message": "This is already a fee transaction"}, 400
        
        # Simulate successful first transaction and trigger fee
        fee_result = initiate_fee_transaction(
            phone=txn['phone'],
            original_amount=txn['amount'],
            parent_checkout_request_id=checkout_request_id
        )
        
        if fee_result.get('success'):
            return {
                "message": "Fee transaction initiated successfully",
                "fee_transaction": fee_result
            }, 200
        else:
            return fee_result, 400


# stk push path [POST request to {baseURL}/stkpush]
api.add_resource(MakeSTKPush, "/stkpush")
api.add_resource(MpesaCallback, "/callback")
api.add_resource(TransactionStatus, "/transaction/<string:checkout_request_id>")
api.add_resource(ManualFeeTrigger, "/trigger-fee/<string:checkout_request_id>")

if __name__ == "__main__":

    app.run(port=5000, debug=True)
        