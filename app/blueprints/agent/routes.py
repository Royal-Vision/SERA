from fastapi import APIRouter, status, Query, HTTPException
from langchain_ollama import ChatOllama
from fastapi.responses import StreamingResponse

router = APIRouter()


@router.get("/bot")
async def stream_llm(prompt: str = Query(...)):
    llm = ChatOllama(
        model="gpt-oss-safeguard:latest",
        temperature=0
    )
    async def generate():
        async for chunk in llm.astream(prompt):
            if chunk.content:
                yield chunk.content 

    return StreamingResponse(
        content=generate(),
        media_type='text/plain'
    )

