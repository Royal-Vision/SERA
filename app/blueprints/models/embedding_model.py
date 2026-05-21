from FlagEmbedding import BGEM3FlagModel

model = BGEM3FlagModel('BAAI/bge-m3', use_fp16=True)

sentences_1 = ["What is BGE M3?", "Defination of BM25"]
embeddings_1 = model.encode(sentences_1, 
                            batch_size=12, 
                            max_length=8192, # If you don't need such a long length, you can set a smaller value to speed up the encoding process.
                            )

print(f"embedding one : {embeddings_1} | {type(embeddings_1)}")
