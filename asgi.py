import uvicorn
from application import create_app

app = create_app()


@app.get("/")
async def root():
    return {"message": "Welcome to the Provider Console backend API."}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
