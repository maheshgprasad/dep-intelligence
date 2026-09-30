const axios = require("axios");
const express = require("express");

const app = express();

async function listUsers(_req, res) {
  await axios.post("/api/auth/validate", { token: "demo" });
  res.json([{ id: 1 }]);
}

app.get("/api/users", listUsers);

module.exports = { app, listUsers };
