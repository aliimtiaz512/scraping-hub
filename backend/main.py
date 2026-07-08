from fastapi import FastAPI, Depends, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from database import engine, Base, get_db
import models
import scraper
from pydantic import BaseModel
import pandas as pd
import os

# Create tables
Base.metadata.create_all(bind=engine)

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

class ScrapeRequest(BaseModel):
    keyword: str


@app.post("/api/scrape")
async def trigger_scrape(req: ScrapeRequest, db: Session = Depends(get_db)):
    try:
        results = await scraper.scrape_bids(req.keyword)
        
        # Clear previous bids from the database to only show the latest scrape
        db.query(models.Bid).delete()
        db.commit()
        
        # Save to database
        saved_count = 0
        seen_refs = set()
        for data in results:
            ref = data.get("reference_number", "")
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            bid = models.Bid(**data)
            db.add(bid)
            saved_count += 1
        
        db.commit()
        return {"status": "success", "message": f"Scraped {len(results)} bids, inserted {saved_count} new records."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/bids")
def get_bids(query: str = "", db: Session = Depends(get_db)):
    if query:
        bids = db.query(models.Bid).filter(
            or_(
                models.Bid.title.ilike(f"%{query}%"),
                models.Bid.solicitation_number.ilike(f"%{query}%"),
                models.Bid.reference_number.ilike(f"%{query}%")
            )
        ).all()
    else:
        bids = db.query(models.Bid).all()
    return bids

@app.get("/api/export")
def export_bids(db: Session = Depends(get_db)):
    bids = db.query(models.Bid).all()
    
    data = []
    for bid in bids:
        data.append({
            "Reference Number": bid.reference_number,
            "Solicitation Number": bid.solicitation_number,
            "Solicitation Type": bid.solicitation_type,
            "Title": bid.title,
            "Publication Date": bid.publication_date,
            "Question Acceptance Deadline": bid.question_acceptance_deadline,
            "Closing Date": bid.closing_date,
            "Documents Count": bid.documents_count
        })
    
    df = pd.DataFrame(data)
    file_path = "bids_export.xlsx"
    df.to_excel(file_path, index=False)
    
    return FileResponse(path=file_path, filename="bids_export.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
