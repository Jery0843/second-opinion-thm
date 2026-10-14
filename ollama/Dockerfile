# Bakes the model into the image at BUILD time (when internet is
# available) so the internal_net service never needs runtime internet
# access -- it stays genuinely internal, matching the room's network
# story.
FROM ollama/ollama:latest

RUN (/bin/ollama serve &) && \
    sleep 5 && \
    /bin/ollama pull llama3.2:1b && \
    sleep 2

EXPOSE 11434
ENTRYPOINT ["/bin/ollama"]
CMD ["serve"]
