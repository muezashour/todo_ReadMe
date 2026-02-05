FROM node:22-alpine

WORKDIR /app

# Copy only package files first for better layer caching
COPY package*.json ./

# Install dependencies (do NOT copy node_modules from host)
RUN npm install

# Copy Prisma schema and generate client inside Linux container
COPY prisma ./prisma
RUN npx prisma generate

# Copy the rest of the application source code
COPY . .

# Expose the app port
EXPOSE 5003

# Start app with migrations applied
CMD ["npm", "run", "dev"]
