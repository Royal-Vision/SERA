# pii_scrubber.py — singleton pattern
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine

class PIIScrubber:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.analyzer = AnalyzerEngine()
            cls._instance.anonymizer = AnonymizerEngine()
        return cls._instance

    def scrub(self, text: str, language: str = "en") -> str:
        results = self.analyzer.analyze(text=text, language=language)
        return self.anonymizer.anonymize(text=text, analyzer_results=results).text

# Usage anywhere in your pipeline
scrubber = PIIScrubber()  # always returns same instance


""" -------------------- Test ------------------------------- """
# user_input = """
# Patient Name: Ahmed Ghoniem
# DOB: 14/06/1989
# Gender: Male
# Emirates ID: 784-1989-1234567-1
# Passport Number: N12345678
# Phone: +971 50 123 4567
# Email: ahmed.ghoniem@example.com
# Address: Marina Tower 4, Apartment 1102, Abu Dhabi

# Chief Complaint:
# Persistent chest pain for 3 days, worsens during exertion.

# Medical History:
# Hypertension
# Type 2 Diabetes

# Current Medications:
# Metformin 500mg twice daily
# Lisinopril 10mg daily"""
# clean_query = scrubber.scrub(user_input)

# print(f"cleaned user input: {clean_query}")
""" ------------------------ test output ----------------------------- """
# cleaned user input: 
# Patient Name: <PERSON>: <DATE_TIME>
# Gender: Male
# Emirates ID: 784-1989-<US_DRIVER_LICENSE>-1
# Passport Number: <US_PASSPORT>
# Phone: <PHONE_NUMBER>
# Email: <EMAIL_ADDRESS>
# Address: <LOCATION>, Apartment <DATE_TIME>, <LOCATION>

# Chief Complaint:
# Persistent chest pain for <DATE_TIME>, worsens during exertion.

# Medical History:
# Hypertension
# Type 2 Diabetes

# Current Medications:
# Metformin 500mg twice daily
# Lisinopril 10mg <DATE_TIME>