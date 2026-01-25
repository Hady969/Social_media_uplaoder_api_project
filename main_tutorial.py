from fastapi import FastAPI, Response, status, HTTPException
from pydantic import BaseModel
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
import time
import requests
import json



app = FastAPI()

# Pydantic model
class Post(BaseModel):
    Platform: str
    Username: str
    CompanyName: str
    password: str

# Connect to PostgreSQL
while True:
    try:
        conn = psycopg2.connect(
            host='localhost',
            database='fastapi socialmedia',
            user='postgres',
            password='hady',
            cursor_factory=RealDictCursor
        )
        cursor = conn.cursor()
        print("Database connection was successful")
        break
    except Exception as error:
        print("Connection to database failed")
        print("Error:", error)
        time.sleep(2)

# In-memory storage (consistent with Post model)
my_posts = [
    {"Platform": "Instagram", "Username": "user1", "CompanyName": "Company1", "password": "pass1", "id": 1},
    {"Platform": "Facebook", "Username": "user2", "CompanyName": "Company2", "password": "pass2", "id": 2},
    {"Platform": "Twitter", "Username": "user3", "CompanyName": "Company3", "password": "pass3", "id": 3}
]

# Utility functions
def find_post(id: int):
    for p in my_posts:
        if p['id'] == id:
            return p

def find_index_post(id: int):
    for i, p in enumerate(my_posts):
        if p['id'] == id:
            return i

# Root endpoint
@app.get("/")
def root():
    return {"message": "Welcome to my API"}

# Get all posts from database
@app.get("/posts")
def get_posts():
    cursor.execute('SELECT * FROM "Accounts"')
    rows = cursor.fetchall()  # rows are already dicts
    print("Fetched rows:", rows)
    return {"data": rows}

# Create new post
@app.post("/posts", status_code=status.HTTP_201_CREATED)
def create_posts(new_post: Post):
    try:
        cursor.execute(
    'INSERT INTO "Accounts" ("Platform", "Username", "password", "Tenant") VALUES (%s, %s, %s, %s) RETURNING *',
    (new_post.Platform, new_post.Username, new_post.password, new_post.CompanyName)
)

        post = cursor.fetchone()
        conn.commit()

        # In-memory list
        new_post_dict = new_post.dict()
        new_post_dict['id'] = post['Id']
        my_posts.append(new_post_dict)

        return {"data": post}

    except Exception as e:
        conn.rollback()
        print("Error inserting post:", e)  # <--- This will show the real cause
        raise HTTPException(status_code=500, detail=str(e))


# Get the most recent post from in-memory list
@app.get("/posts/recent")
def get_latest_post():
    post = my_posts[-1]  # shorthand for len(my_posts)-1
    return {"detail": post}

# Get a post by ID (in-memory)
@app.get("/posts/{id}")
def get_posts_by_id(id: int, response: Response):
    cursor.execute('SELECT * FROM "Accounts" WHERE "Id" = %s',(str(id),))
    post = cursor.fetchone()
    print(post)
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Post with id {id} was not found"
        )
    return {"post_detail": post}

# Delete a post by ID (in-memory)
@app.delete("/posts/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_posts(id: int):
    index = find_index_post(id)
    if index is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Post with id {id} does not exist"
        )
    my_posts.pop(index)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

# Update a post by ID (in-memory)
@app.put("/posts/{id}")
def update_post(id: int, post: Post):
    index = find_index_post(id)
    if index is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Post with id {id} does not exist"
        )
    
    post_dict = post.dict()
    post_dict['id'] = id
    my_posts[index] = post_dict
    return {"data": post_dict}
