FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci --only=production

COPY . .
RUN npm run build

EXPOSE 3001

CMD ["node", "dist/server.js"]
```

5. Save it

---

**Also create a `.dockerignore` in the same folder:**
```
node_modules
dist
.env
*.log
.git