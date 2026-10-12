"""Function words that carry no evidence in a lexical match.

PostgreSQL has no Bengali dictionary and the `simple` configuration used for the index keeps every
word, so a question such as "What is the capital of France?" would match every chunk that happens
to contain "the" or "is" and push those chunks into the fused ranking. The lexical retriever drops
the words below from the *query* (never from the index, so quoted phrases still match exactly).
The lists are short, conventional function-word lists for the two languages the platform targets;
they are not derived from the evaluation data.
"""

ENGLISH_STOPWORDS: frozenset[str] = frozenset("""
    a about above after again all also am an and any are as at be because been before being below
    between both but by can could did do does doing down during each few for from further had has
    have having he her here hers him his how i if in into is it its just me more most my no nor not
    of off on once only or other our out over own same she should so some such than that the their
    them then there these they this those through to too under until up very was we were what when
    where which while who whom why will with would you your yours
    """.split())  # noqa: SIM905

BANGLA_STOPWORDS: frozenset[str] = frozenset("""
    এবং ও কী কি কে কেন কখন কবে কোথায় কোথা কীভাবে কত কোন কোনো কোনও কার কাকে এই ওই এটা এটি ওটা এর তার
    তাদের তা তাই না নয় হয় হবে হলে হচ্ছে হয়েছে করে করা করতে করেন করি থেকে জন্য সঙ্গে সাথে যে যা যখন
    যদি আর তবে কিন্তু বা অথবা আছে আছেন নাকি মধ্যে পর পরে আগে এখন তখন একটি একটা এক থাকে থাকা দিয়ে দিতে
    যায় যাবে যেতে নিয়ে ওর আমি আমরা আপনি তুমি সে তারা আমার আমাদের আপনার
    """.split())  # noqa: SIM905

STOPWORDS: frozenset[str] = ENGLISH_STOPWORDS | BANGLA_STOPWORDS
