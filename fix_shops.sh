#!/bin/bash
FILE="backend/app/routers/shops.py"
cp "$FILE" "$FILE.bak"
sed -i '' 's|@router.get("/shops")|@router.get("/")|g' "$FILE"
sed -i '' 's|@router.post("/shops")|@router.post("/")|g' "$FILE"
sed -i '' 's|@router.put("/shops/|@router.put("/|g' "$FILE"
sed -i '' 's|@router.delete("/shops/|@router.delete("/|g' "$FILE"
sed -i '' 's|@router.post("/shops/|@router.post("/|g' "$FILE"
echo "✅ Fixed! Backup: $FILE.bak"
