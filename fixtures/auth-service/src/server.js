const express = require("express");

const app = express();

function unusedHelper(value) {
  return value;
}

app.post("/api/auth/validate", (req, res) => {
  res.json({ ok: true });
});

app.listen(3001);

module.exports = { app };
