import os
import smtplib
from dotenv import load_dotenv

# Load .env file
load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")

smtp = smtplib.SMTP("smtp.gmail.com", 587)
smtp.set_debuglevel(1)
smtp.ehlo()
smtp.starttls()
smtp.ehlo()
smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)

smtp.sendmail(
    EMAIL_SENDER,
    EMAIL_SENDER,
    "Subject: Gmail Test\n\nThis is a test from the Raspberry Pi."
)

smtp.quit()
print("Email test finished.")
