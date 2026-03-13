Microservices Project

This project is a distributed microservices application for managing todos with authentication and notifications. It demonstrates a microservice architecture with an API Gateway, service-to-service communication, and basic authentication.

Overview

The application consists of the following services:
	1.	API Gateway
	•	Single entry point for all client requests
	•	Routes requests to the appropriate microservice
	•	Can handle authentication checks before forwarding
	2.	Auth Service
	•	Handles user registration and login
	•	Generates JWT tokens for authentication
	•	Runs on port 5001
	3.	Todo Service
	•	CRUD operations for todos
	•	Protected routes using authMiddleware
	•	Can send notifications to Notification Service
	•	Runs on port 5002
	4.	Notification Service
	•	Receives notification events from other services
	•	Logs notifications or sends alerts
	•	Runs on port 5003

Features
	•	User authentication with JWT tokens
	•	CRUD operations on todos
	•	Service-to-service communication (REST)
	•	API Gateway routing
	•	Notification system
	•	Microservice architecture demonstration

Technologies Used
	•	Node.js
	•	Express.js
	•	JWT for authentication
	•	axios for inter-service HTTP requests
	•	http-proxy-middleware for API Gateway
	•	Optional: Docker for containerization

Folder Structure

microservices-project/
├── api-gateway/
│   └── server.js
├── auth-service/
│   ├── src/
│   │   ├── controllers/authController.js
│   │   ├── routes/authRoutes.js
│   │   └── server.js
│   └── package.json
├── todo-service/
│   ├── src/
│   │   ├── controllers/todoController.js
│   │   ├── routes/todoRoutes.js
│   │   ├── middleware/authMiddleware.js
│   │   └── server.js
│   └── package.json
├── notification-service/
│   ├── src/
│   │   ├── controllers/notificationController.js
│   │   ├── routes/notificationRoutes.js
│   │   └── server.js
│   └── package.json
└── docker-compose.yml

Installation

Clone the repository:

git clone https://github.com/muezashour/todo_ReadMe.git
cd microservices-project

Install dependencies for each service:

cd auth-service && npm install
cd ../todo-service && npm install
cd ../notification-service && npm install
cd ../api-gateway && npm install

Running the Project

Without Docker

Start each service in separate terminals:

# Auth Service
cd auth-service
node src/server.js

# Todo Service
cd todo-service
node src/server.js
git add README.md
# Notification Service
cd notification-service
node src/server.js

# API Gateway
cd api-gateway
node server.js

With Docker (Optional)

Run all services together:

docker-compose up --build

API Endpoints

Auth Service
	•	POST /auth/register
	•	POST /auth/login

Todo Service (Protected by JWT)
	•	GET /todos
	•	POST /todos
	•	PUT /todos/:id
	•	DELETE /todos/:id

Notification Service
	•	POST /notify

API Gateway Routes
	•	/auth → Auth Service
	•	/todos → Todo Service
	•	/notify → Notification Service

Inter-Service Communication
	•	Todo Service calls Notification Service via HTTP POST when a new todo is created.
	•	API Gateway forwards client requests to the appropriate service based on the URL prefix.

Author
                     ┌─────────────────┐
                     │     Client      │
                     │ (Web / Mobile)  │
                     └────────┬────────┘
                              │
                              ▼
                     ┌─────────────────┐
                     │   API Gateway   │
                     │  (Port 5000)    │
                     └────────┬────────┘
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
  ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
  │ Auth Service  │ │ Todo Service  │ │ Notification  │
  │  Port 5001    │ │  Port 5002    │ │  Port 5003    │
  └──────┬────────┘ └──────┬────────┘ └──────┬────────┘
         │                 │                 │
         ▼                 ▼                 ▼
   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
   │  User DB    │   │  Todos DB   │   │ Notification│
   │  (Prisma)   │   │  (Prisma)   │   │  Logs / MQ │
   └─────────────┘   └─────────────┘   └─────────────┘
Abdulmuez Ashour
GitHub: https://github.com/muezashour
