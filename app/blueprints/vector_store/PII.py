from presidio_analyzer import AnalyzerEngine

text = "His name is Mr. Jones and his phone number is 212-555-5555"

analyzer = AnalyzerEngine()
analyzer_results = analyzer.analyze(text=text, language="en")

print(analyzer_results)
print("------------------------------")
titles_list = [
    "Sir",
    "Ma'am",
    "Madam",
    "Mr.",
    "Mrs.",
    "Ms.",
    "Miss",
    "Dr.",
    "Professor",
]


from presidio_analyzer import PatternRecognizer

titles_recognizer = PatternRecognizer(supported_entity="TITLE", deny_list=titles_list)
text1 = """My patient Ahmed Ghoniem visited Cleveland Clinic Abu Dhabi on 12 March 2026.
His Emirates ID is 784-1989-1234567-1.
His phone number is +971501234567.
Email: ahmed@example.com

Please summarize his diagnosis history and suggest follow-up actions."""
analyzer_results = analyzer.analyze(text=text1, language="en")

print(analyzer_results)