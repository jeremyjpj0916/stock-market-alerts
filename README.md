### Hoping to solve pre/post/regular market hours alerts on big stock moves(indicating a catalyst has occured).


Script will use yahoo api via lib, and SMS texts via a lib to help keep you in the know for when a big move happens(FDA Approval/Deny, Data good/bad for biotech plays).

1. Compare premarket/postmarket/regular % change >= .10 (10%) movement either way means we need to alert. This can be configurable.
2. Alert will SMS text you the ticker and current seen price and the % moveement it saw at the time of alert/query.

This code should just run in the background on your computer making the checks.

Note, for text from gmail functionality to work, enable the "allow less secure apps" login feature tied to your gmail account. Maybe use a spare gmail if you have security concerns.
