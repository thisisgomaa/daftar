# Principles

What seven months of running AI agents on real work taught us, before any of it became Daftar.
Each line comes from something that actually happened.

1. **One capable agent beats a team of specialists.**
   We started with a "team": a project manager, a strategist, a security agent, a dozen specialists. Most of the specialists were never called. What works is one strong generalist that focuses on one thing per session, not an agent that does one thing forever.

2. **The agent never rebuilds itself.**
   Changes to the system (the bridge, the rules, the services) are made from a separate Claude Code session outside it. An agent editing the house it lives in eventually breaks it while standing inside.

3. **Build step by step.**
   Our first setup shipped with 18 scheduled jobs; five turned out to matter. Start with the smallest thing that works and add only when you feel the need.

4. **Don't move your whole life in at once.**
   Bring work in project by project, and explain each one properly before the next. A half-explained project is worse than none.

5. **Official beats clever.**
   Third-party agent frameworks were faster to start with, then a provider changed its terms and the ground moved. Build on the vendor's own tools and your own account.

6. **Files are the memory, not the chat.**
   Sessions end, models change, chats get lost. Anything that matters is written to a file the moment it happens: decisions, lessons, where each project stands.

7. **Simple memory wins.**
   We tried a vector database. It went quiet and nobody missed it. A small map plus plain-text topic files has worked every day since.

8. **One source for the rules.**
   Our rules once lived in five files with five wordings, and one stale copy caused duplicate replies. One file, injected everywhere.

9. **Notify when there's something to do.**
   A daily status digest became noise and was killed. Push a message when an action is needed, not on a schedule.

10. **Don't run many agents at once.**
    Nine parallel agents hit the usage limit and stopped everything for hours. Two or three at a time.

11. **After two rejections, stop and ask.**
    A third attempt on the same broken foundation is wasted work. Say what looks wrong, offer at most two options, and let the owner decide.
