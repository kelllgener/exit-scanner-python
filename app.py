import cv2
import numpy as np
import aiohttp
import asyncio
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import base64
import firebase_admin
from firebase_admin import credentials, firestore

# Define the IP address of your ESP32
ESP32_IP = '192.168.18.18'

# Initialize Firebase Admin SDK
cred = credentials.Certificate("firebase-credentials.json")
firebase_admin.initialize_app(cred)

db = firestore.client()

# AES key (must be 16 bytes)
SECRET_KEY = b'Todamoon_drivers'

async def decrypt(encrypted_data, key):
    try:
        cipher = AES.new(key, AES.MODE_ECB)
        encrypted_data_bytes = base64.b64decode(encrypted_data)
        decrypted_data = unpad(cipher.decrypt(encrypted_data_bytes), AES.block_size)
        return decrypted_data.decode('utf-8')
    except Exception as e:
        await trigger_red_led()
        await trigger_buzzer_on_esp32()
        print("Exit Error: Invalid QR Code")
        return None

async def fetch_user_data_from_firestore(user_id):
    try:
        user_ref = db.collection("users").document(user_id)
        user_doc = user_ref.get()
        return user_doc.to_dict() if user_doc.exists else None
    except Exception as e:
        print(f"Error fetching user data: {e}")
        return None

async def fetch_terminal_fee():
    try:
        fee_ref = db.collection("dashboard-counts").document("terminal-fee")
        fee_doc = fee_ref.get()
        return fee_doc.to_dict().get("fee") if fee_doc.exists else None
    except Exception as e:
        print(f"Error fetching terminal fee: {e}")
        return None

async def trigger_buzzer_on_esp32(session):
    try:
        buzzer_url = f'http://{ESP32_IP}/activate_buzzer'
        async with session.get(buzzer_url) as response:
            if response.status != 200:
                print("Failed to trigger the buzzer")
    except Exception as e:
        print(f"Error triggering buzzer: {e}")

async def trigger_green_led(session):
    try:
        buzzer_url = f'http://{ESP32_IP}/green_led'
        async with session.get(buzzer_url) as response:
            if response.status != 200:
                print("Failed to trigger the green led")
    except Exception as e:
        print(f"Error triggering green led: {e}")

async def trigger_red_led(session):
    try:
        buzzer_url = f'http://{ESP32_IP}/red_led'
        async with session.get(buzzer_url) as response:
            if response.status != 200:
                print("Failed to trigger the red led")
    except Exception as e:
        print(f"Error triggering red led: {e}")

async def exit_queue(user_data, session):
    user_id = user_data['uid']
    barangay_name = user_data.get('barangay', 'default_barangay')
    user_ref = db.collection("users").document(user_id)
    barangay_ref = db.collection("barangays").document(barangay_name)
    queue_ref = barangay_ref.collection("queue")
    history_ref = db.collection("queueing_history")

    try:
        user_doc = user_ref.get()
        if not user_doc.exists:
            await trigger_red_led(session)
            await trigger_buzzer_on_esp32(session)
            print(f"User with UID {user_id} does not exist.")
            return

        user_data = user_doc.to_dict()
        if not user_data.get("inQueue"):
            await trigger_red_led(session)
            await trigger_buzzer_on_esp32(session)
            print("User is not in the queue.")
            return

        # Create a batch for Firestore operations
        batch = db.batch()

        # Update user inQueue status
        batch.update(user_ref, {"inQueue": False})
        # Remove user from queue
        batch.delete(queue_ref.document(user_id))

        # Transaction record
        transaction_data = {
            "amount": 0,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "description": "Left Queue"
        }
        user_ref.collection("queueing-transactions").document().set(transaction_data)

        # History log
        history_data = {
            "driverId": user_id,
            "name": user_data.get("name"),
            "barangay": barangay_name,
            "action": "leave",
            "timestamp": firestore.SERVER_TIMESTAMP
        }
        history_ref.document().set(history_data)

        # Commit the batch (without await)
        batch.commit()

        await trigger_green_led(session)
        await trigger_buzzer_on_esp32(session)
        print(f"Left queue successfully!")

    except Exception as e:
        await trigger_red_led(session)
        print(f"Error: {e}")


def parse_qr_data(decrypted_data):
    return dict(line.split(": ", 1) for line in decrypted_data.split("\n") if ": " in line)

async def main():
    url = f'http://{ESP32_IP}/capture'
    detector = cv2.QRCodeDetector()
    prev_data = ""

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # Fetch frame from ESP32-CAM
                async with session.get(url, timeout=5) as img_resp:
                    if img_resp.status != 200:
                        print("Failed to get image from ESP32. Retrying...")
                        await asyncio.sleep(5)  # Wait before retrying
                        continue

                    imgnp = np.array(bytearray(await img_resp.read()), dtype=np.uint8)
                    frame = cv2.imdecode(imgnp, cv2.IMREAD_COLOR)

                    if frame is None:
                        print("Failed to decode image, skipping...")
                        await asyncio.sleep(1)  # Delay before next attempt
                        continue

                    # Detect and decode QR code
                    data, points, _ = detector.detectAndDecode(frame)

                    if data and data != prev_data:
                        print(f"QR Code Data: {data}")
                        decrypted_data = await decrypt(data, SECRET_KEY)
                        if decrypted_data:
                            qr_data = parse_qr_data(decrypted_data)
                            user_id = qr_data.get("uid")

                            if user_id:
                                print(f"Fetching data for user ID: {user_id}")
                                user_data = await fetch_user_data_from_firestore(user_id)
                                if user_data:
                                    await exit_queue(user_data, session)

                        prev_data = data

                await asyncio.sleep(0.1)  # Throttle frame processing

            except asyncio.TimeoutError:
                print("Request timed out. Retrying...")
                await asyncio.sleep(5)  # Wait before retrying

            except Exception as e:
                print(f"Error in main loop: {e}")
                await asyncio.sleep(1)  # Delay on error

if __name__ == "__main__":
    asyncio.run(main())

