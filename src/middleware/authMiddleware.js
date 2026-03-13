import jwt from 'jsonwebtoken';



function authMiddleware(req, res, next) {
    const token = req.headers['authorization']
    if (!token) {
        return res.status(401).json({ message: 'No token provided' });
    }
    jwt.verify(token, process.env.JWT_SECRET, (err, decode) => {
        if (err) {
            return res.status(401).json({message: 'invalid token '})
        }
        req.userId = decode.id
        next();
    })
}


export default authMiddleware;
