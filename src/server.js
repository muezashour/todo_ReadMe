import express from 'express';
import path, { dirname } from 'path';
import { fileURLToPath } from 'url';
import authRoutes from './routets/authRoutes.js';
import todoRoutes from './routets/todoRoutes.js';
import authMiddleware from './middleware/authMiddleware.js';

const app = express()
const port = process.env.PORT || 5003

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

// Middleware
app.use(express.json())
app.use(express.static(path.join(__dirname, '../public')))

app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'))
})
app.use('/auth', authRoutes)
app.use('/todos', authMiddleware, todoRoutes)

app.listen(port, "0.0.0.0", () => {
    console.log(`Server is running on http://localhost:${port}`);
});

